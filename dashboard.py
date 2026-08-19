#!/usr/bin/env python3
"""Flask dashboard for the market-zone index — the visual layer on top of the
engine. Mirrors the house pattern (crypto-dashboard :8768, escalera :8765):
binds to 127.0.0.1, reached over an SSH tunnel.

Semantics (per design decision): the score is computed ONCE over the full
loaded history; the range buttons only ZOOM the axis client-side, so a given
date never changes zone when you switch ranges.
"""
from __future__ import annotations

import csv
import gzip
import html
import io
import json
import math
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory

import geo
from cartera.exposure import by_asset_class as _by_asset_class
from cartera.exposure import by_economic_currency as _ccy_economic
from cartera.exposure import by_quote_currency as _ccy_quote
from cartera.fiscal import TRAMOS_ANO as _TRAMOS_ANO
from cartera.fiscal import loss_offset_note as _loss_note
from cartera.fiscal import repurchase_risk as _repurchase_risk
from cartera.fiscal import simulate_sale as _simulate_sale

# El dominio de la cartera vive en su propio paquete: qué significa un
# movimiento no depende de que haya un navegador delante. Aquí dentro siguen
# siendo detalles internos, así que se reexportan con el guion bajo con el que
# los llama el resto de este fichero.
from cartera.parsing import CARTERA_EXPORT_COLS, COLSYN
from cartera.parsing import csv_num as _csv_num
from cartera.parsing import instrument_kind as _instrument_kind
from cartera.parsing import looks_like_isin as _looks_like_isin
from cartera.parsing import mov_key as _mov_key
from cartera.parsing import name_from_meta as _name_from_meta
from cartera.parsing import norm_col as _norm_col
from cartera.parsing import norm_date as _norm_date
from cartera.parsing import norm_side as _norm_side
from cartera.parsing import num as _num
from cartera.parsing import side_es as _side_es
from cartera.parsing import sniff_sep as _sniff_sep
from cartera.parsing import symbol_isin as _symbol_isin
from cartera.plan import attention as _attention
from cartera.plan import contribution_stats as _contrib_stats
from cartera.plan import diary as _diary
from cartera.plan import goal_progress as _goal_progress
from cartera.plan import monthly_flows as _monthly_flows
from cartera.positions import BASE_CCY
from cartera.positions import compute as _compute_positions
from cartera.returns import beta as _beta
from cartera.returns import drawdown as _drawdown
from cartera.returns import effective_n as _effective_n
from cartera.returns import nav_series as _nav_series
from cartera.returns import rebalance_with_cash as _rebalance
from cartera.returns import sharpe as _sharpe
from cartera.returns import twr as _twr
from cartera.returns import volatility as _volatility
from cartera.returns import xirr as _xirr
from cartera.splits import cost_is_preserved as _cost_ok
from cartera.splits import pending as _splits_pending
from cartera.splits import preview as _splits_preview

# The regime panel reuses its own builder + cache (import is side-effect-free;
# its prewarm/run only fire under __main__, which we never trigger here).
from regime.dashboard import CURATED as REGIME_CURATED
from regime.dashboard import _get as regime_get
from zones import WEEKLY, BadSymbol, NoHistory, analyze, fetch_daily, safe_symbol, to_weekly
from zones.engine import VOL_W_DEFAULT
from zones.target import compute as _compute_target

app = Flask(__name__)
# Long-lived static caching is only safe with content-addressed URLs, so the
# version stamp comes from the file's own mtime (see `asset()`). Hand-bumped
# `?v=N` had already drifted — the same regime.css was requested as v7 by two
# templates and v8 by two others, so browsers cached it twice and any edit that
# forgot the bump would have gone unnoticed.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000       # 1 year

# ── DNS rebinding ─────────────────────────────────────────────────────────
# Binding to 127.0.0.1 stops the network from reaching this service; it does
# nothing about a name that RESOLVES to 127.0.0.1. An attacker serves a page
# from evil.example with a one-second TTL, re-points the name at loopback, and
# their script fetches http://evil.example:8771/api/cartera — which the browser
# now treats as SAME-ORIGIN, so it can read every response. No CORS rule applies
# and the CSRF header can be set freely, because nothing is cross-origin any
# more. The one thing that does not change is the Host header: it still carries
# the attacker's name.
#
# Werkzeug rejects a mismatch with 400 before any view runs, and the check
# covers GET as well — which is the point, since reads are what rebinding is
# for. The port is ignored, so `ssh -L 9000:127.0.0.1:8771` keeps working.
#
# `::1` is deliberately absent: Werkzeug strips the port with
# `partition(":")[0]`, which turns `[::1]:8771` into `[`, and adding `[::1]`
# to the list would then match ANY bracketed IPv6 literal. The service binds
# IPv4-only, so this costs nothing today — but revisit it if the bind address
# ever changes.
#
# PUBLIC_HOST widens this by exactly ONE name — the one a proxy serves this
# instance under. It is not a hole in the check: a rebinding attacker's domain
# still does not match, which is the whole job. Unset, this is loopback-only,
# exactly as it has always been.
PUBLIC_HOST = (os.environ.get("PUBLIC_HOST") or "").strip().lower()
app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"] + ([PUBLIC_HOST] if PUBLIC_HOST else [])

# ── Upload limits ─────────────────────────────────────────────────────────
# The import path used to accept a body of any size and then walk it row by
# row at ~150 us each (a `pd.to_datetime` per row is most of that). A 10 MB
# CSV kept a worker busy for over two minutes and left the service
# unresponsive; a larger one would have hit MemoryMax and killed it. Three
# independent ceilings, because each bounds a different resource:
MAX_UPLOAD_MB = 4               # bytes read into memory (Werkzeug returns 413)
MAX_UPLOAD_ROWS = 5000          # rows walked in Python (a real book is ~250)
MAX_ISIN_LOOKUPS = 50           # outbound Yahoo searches per file
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# One codebase, several instances: each person gets their own process on their
# own port, behind a proxy that maps a login to one of them. Unset means the
# port this service has always used, so an existing deployment is unaffected.
PORT = int(os.environ.get("MZ_PORT", "8771"))
YEARS = 25              # how far back we load (score normalizes over this window)
CACHE_TTL = 600         # seconds; avoids hammering Yahoo (and its 429s)

# Curated dropdown; users can also type any Yahoo ticker.
CURATED = [
    # Uranio / nuclear
    ("NLR", "VanEck Uranio y Nuclear"),
    ("URA", "Global X Uranium"),
    ("URNM", "Sprott Uranium Miners"),
    ("CCJ", "Cameco"),
    # Índices de renta variable
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("^RUT", "Russell 2000"),
    ("URTH", "MSCI World (URTH)"),
    ("EEM", "Emerging Markets (EEM)"),
    ("^N225", "Nikkei 225"),
    ("^KS11", "KOSPI"),
    ("^HSCE", "HSCEI (China H)"),
    # Materias primas / metales
    ("GLD", "Oro"),
    ("SLV", "Plata"),
    ("BZ=F", "Petróleo Brent"),
    # Mineras de oro
    ("GDX", "Mineras oro senior (GDX)"),
    ("GDXJ", "Mineras oro junior (GDXJ)"),
    # Acciones / otros
    ("UNH", "UnitedHealth (UNH)"),
    ("KOS", "Kosmos Energy (KOS)"),
    ("HGRAF", "HydroGraph (HGRAF)"),
    ("BTC-USD", "Bitcoin"),
]

# What gets cached is the RESPONSE BYTES, never the payload object. A built
# regime payload costs ~3.4 MB as live Python objects and 75 KB gzipped — a 45x
# difference. Holding dicts, a 64-entry cap on each cache allowed ~500 MB
# against the unit's MemoryMax=300M, so the cap was bounding the wrong quantity.
# Caching the encoding also removes the per-request gzip, which WAS the response
# time on a cache hit (12-25 ms).
# Segundos que /api/cartera/zonas dedica a calcular zonas frías antes de
# devolver lo que lleve y dejar el resto en `pending`. Por debajo del timeout de
# cliente del panel, para que una cartera grande responda a trozos en vez de
# agotarse entera.
ZONES_BUDGET_S = 8.0
CACHE_MAX = 64                    # entry cap for the small metadata caches
CACHE_BUDGET = 48 * 1024 * 1024   # bytes held by the payload cache
_cache: dict[str, tuple[float, bytes, bytes]] = {}    # key -> (ts, raw, gzip)
_cache_bytes = 0
_lock = threading.Lock()
_locks: dict[str, threading.Lock] = {}   # per-symbol single-flight


def _cache_put(cache: dict, key, value, cap: int = CACHE_MAX) -> None:
    """Insert and evict oldest-first. Callers can type any ticker, so an
    unbounded cache is a slow leak under the unit's MemoryMax."""
    cache[key] = value
    while len(cache) > cap:
        cache.pop(next(iter(cache)))         # dicts keep insertion order


def _payload_put(key: str, raw: bytes, gz: bytes) -> None:
    """Insert into the payload cache under a BYTE budget, evicting oldest-first.

    Counting entries is the wrong bound when entries differ by 50x: a 64-entry
    cap was simultaneously too generous for memory (it once permitted ~500 MB of
    live payloads) and too tight to hold the 69 items the prewarm builds, so the
    warmed set evicted itself and the panel rebuilt on every visit. Bound the
    quantity that actually matters.
    """
    global _cache_bytes
    prev = _cache.pop(key, None)
    if prev is not None:
        _cache_bytes -= len(prev[1]) + len(prev[2])
    _cache[key] = (time.time(), raw, gz)
    _cache_bytes += len(raw) + len(gz)
    while _cache_bytes > CACHE_BUDGET and len(_cache) > 1:
        k = next(iter(_cache))               # dicts keep insertion order
        old = _cache.pop(k)
        _cache_bytes -= len(old[1]) + len(old[2])


# ── Rate limiting ─────────────────────────────────────────────────────────
# Loopback binding and the Host check stop a foreign page from READING, but a
# cross-origin GET still reaches the service — the browser only hides the
# response. So any page the user visits can make this server fetch from Yahoo
# and burn CPU in a loop, and a runaway script of their own does the same.
#
# What is metered is the EXPENSIVE path only: a cache hit costs nothing and is
# never charged, so normal browsing never sees the limiter. The prewarm is
# exempt (it calls the builders directly, not through a route).
UPSTREAM_RATE = 1.0     # sustained builds per second
UPSTREAM_BURST = 40     # enough to open every panel at once from cold


class TokenBucket:
    """Classic token bucket: `burst` at rest, refilling at `rate` per second."""

    def __init__(self, rate: float, burst: int):
        self.rate, self.burst = rate, burst
        self._tokens = float(burst)
        self._ts = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: int = 1) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.burst, self._tokens + (now - self._ts) * self.rate)
            self._ts = now
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def retry_after(self) -> int:
        with self._lock:
            return max(1, int((1.0 - self._tokens) / self.rate) + 1)


_upstream = TokenBucket(UPSTREAM_RATE, UPSTREAM_BURST)


class TooBusy(RuntimeError):
    """The expensive path is rate limited right now."""


def _busy():
    """429 with the honest wait, so a client can back off instead of hammering."""
    r = jsonify({"error": "Demasiadas peticiones nuevas. Espera unos segundos."})
    r.status_code = 429
    r.headers["Retry-After"] = str(_upstream.retry_after())
    return r


def _encoded(key: str, build, limited: bool = False):
    """(raw, gzip) bytes for a payload, cached with single-flight.

    `build` is called at most once per key per TTL, and its result is dropped as
    soon as it is serialized — only the bytes survive. With `limited=True` a
    MISS spends a token and raises `TooBusy` when there are none; a hit is
    always free, so the limiter is invisible until someone starts looping.
    """
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1], hit[2]
        lk = _get_lock(key)
    # Single-flight: concurrent requests for the same key wait here and then
    # read the fresh entry, instead of each firing its own slow Yahoo call.
    with lk:
        with _lock:
            hit = _cache.get(key)
            if hit and time.time() - hit[0] < CACHE_TTL:
                return hit[1], hit[2]
        # Charged after the second look, so requests that queued behind a
        # single-flight build and found it ready are not billed for it.
        if limited and not _upstream.take():
            raise TooBusy
        raw = json.dumps(build(), separators=(",", ":")).encode()
        gz = gzip.compress(raw, 6)
        with _lock:
            _payload_put(key, raw, gz)
        return raw, gz


def _get_lock(key) -> threading.Lock:
    """Get-or-create, without building a throwaway Lock on every hit.
    Prunes only idle locks for keys no longer cached — never a held one."""
    lk = _locks.get(key)
    if lk is None:
        if len(_locks) > 4 * CACHE_MAX:
            for k in [k for k, v in _locks.items()
                      if k not in _cache and not v.locked()]:
                _locks.pop(k, None)
        lk = _locks[key] = threading.Lock()
    return lk


API_MAX_AGE = 120       # browser-side; server cache is CACHE_TTL
_ASSET_V: dict[str, str] = {}

# ── CSRF ──────────────────────────────────────────────────────────────────
# Binding to 127.0.0.1 keeps the NETWORK out; it does nothing about the user's
# own browser. Any page they visit while the tunnel is open can POST here, and
# a cross-site form is a "simple request" — no CORS preflight to stop it. That
# is enough to wipe the portfolio with an empty body.
#
# Two independent barriers, because this service has no session to hang a token
# on:
#   1. A NON-SAFELISTED request header. Forms cannot set headers at all, and a
#      fetch() that sets one forces a preflight, which fails: no CORS headers
#      are served. This alone blocks every cross-site write.
#   2. Origin/Referer must resolve to a loopback host when present. Also stops
#      a DNS-rebinding origin from writing, since its host is not loopback.
# Absent Origin AND Referer means no browser context (curl, a script): those
# still have to send the header, so the check is not weakened.
CSRF_HEADER = "X-Market-Zones"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
# Served under a name, the browser sends that name as Origin, and refusing it
# would refuse every legitimate write. The set stays CLOSED: one more origin,
# not "any origin" — a form on another site is still rejected, which is the
# only thing this gate was ever for.
ALLOWED_ORIGIN_HOSTS = LOOPBACK_HOSTS | ({PUBLIC_HOST} if PUBLIC_HOST else set())


def _origin_is_local(req) -> bool:
    """True unless Origin/Referer says the request came from another site."""
    for header in ("Origin", "Referer"):
        value = req.headers.get(header)
        if not value:
            continue
        try:
            host = urllib.parse.urlsplit(value).hostname
        except ValueError:
            return False
        return host in ALLOWED_ORIGIN_HOSTS
    return True                       # no browser context to judge


@app.errorhandler(413)
def _too_large(_e):
    """Werkzeug aborts the request as soon as the body passes the limit, so the
    oversized data is never read into memory. Answer in the shape the client
    already parses instead of an HTML error page."""
    return jsonify({"error": f"El archivo supera el límite de {MAX_UPLOAD_MB} MB."}), 413


@app.before_request
def _csrf_guard():
    """Gate every state-changing request. Registered globally rather than as a
    per-route decorator so a new mutating endpoint is protected by default —
    forgetting the decorator is exactly how this class of hole reappears."""
    if request.method in SAFE_METHODS:
        return None
    if not _origin_is_local(request):
        return jsonify({"error": "Petición rechazada: origen externo."}), 403
    if request.headers.get(CSRF_HEADER) != "1":
        return jsonify({"error": "Petición rechazada: falta la cabecera "
                                 f"{CSRF_HEADER}."}), 403
    return None


# ── La cartera no se sirve a ciegas ───────────────────────────────────────
# Esta aplicación no autentica a nadie, y nunca ha hecho falta: el aislamiento
# es el loopback más un túnel SSH. `PUBLIC_HOST` es la única variable que rompe
# esa premisa — abre el panel a un nombre servido por un proxy — y el bloque que
# la define da por supuesto "un proxy que mapea un login". Ese proxy vive fuera
# de este repositorio, así que aquí dentro no hay forma de comprobar que exista.
#
# Lo que sí se puede hacer es negarse a servir el libro de movimientos a quien
# nunca ha declarado que hay un login delante. No es autenticación y no pretende
# serlo: cierra el fallo de CONFIGURACIÓN —poner PUBLIC_HOST sin caer en que la
# cartera queda colgada de él— y no al atacante que ya haya atravesado el proxy.
# Esa distinción importa; no vale confundir un cerrojo con una cerradura.
#
# Falla CERRADO. El resto del panel (zonas, régimen, screener) son precios
# públicos y siguen sirviéndose: lo único que se retira es el dato de alguien.
CARTERA_BEHIND_AUTH = (os.environ.get("CARTERA_BEHIND_AUTH") or "").strip() == "1"
CARTERA_PATHS = ("/cartera", "/api/cartera")
_CARTERA_LOCKED_MSG = (
    "La cartera no se sirve bajo un nombre público sin declarar que hay "
    "autenticación delante. Si este panel está detrás de un proxy que exige "
    "login, arranca el servicio con CARTERA_BEHIND_AUTH=1. Si no lo está, no "
    "pongas PUBLIC_HOST: en loopback la cartera funciona sin nada de esto.")


def _is_cartera_path(path: str) -> bool:
    """Comparación por segmento, no por prefijo de texto.

    `startswith("/api/cartera")` daría por cubierta `/api/carteras-publicas` —
    o al revés, dejaría fuera una ruta nueva por un guion. El límite es la
    barra o el final de la cadena.
    """
    return any(path == p or path.startswith(p + "/") for p in CARTERA_PATHS)


@app.before_request
def _cartera_guard():
    """Registrado global, no como decorador por ruta, por la misma razón que el
    de CSRF: una ruta nueva bajo /api/cartera nace protegida. Olvidar el
    decorador es exactamente como reaparece este agujero."""
    if not PUBLIC_HOST or CARTERA_BEHIND_AUTH:
        return None
    if not _is_cartera_path(request.path):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": _CARTERA_LOCKED_MSG}), 403
    # La página la abre una persona con un navegador: se le contesta en HTML,
    # igual que el manejador del 413 contesta en la forma que el cliente ya
    # sabe leer.
    return Response(
        "<!doctype html><meta charset=utf-8><title>Cartera no disponible</title>"
        "<p style='font:16px/1.6 system-ui;max-width:38em;margin:4em auto;padding:0 1em'>"
        + html.escape(_CARTERA_LOCKED_MSG) + "</p>",
        status=403, mimetype="text/html; charset=utf-8")


@app.template_global()
def asset(path: str) -> str:
    """`/static/<path>?v=<mtime>` — the URL changes exactly when the file does.

    Edit a file and every page picks it up on the next load; leave it alone and
    the browser never asks again. Resolved once per process: these files do not
    change under a running service.
    """
    v = _ASSET_V.get(path)
    if v is None:
        full = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", path)
        try:
            v = str(int(os.path.getmtime(full)))
        except OSError:
            v = "0"
        _ASSET_V[path] = v
    return f"/static/{path}?v={v}"


def _bytes_response(raw: bytes, gz: bytes, max_age: int = API_MAX_AGE) -> Response:
    """Serve pre-encoded bytes, picking the form the client accepts.

    The Cache-Control header is what makes moving BETWEEN pages feel instant:
    each page is a full document load, so without it every navigation re-fetches
    payloads the browser already has and the server would hand back unchanged.
    """
    if "gzip" in request.headers.get("Accept-Encoding", ""):
        resp = Response(gz, mimetype="application/json")
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
    else:
        resp = Response(raw, mimetype="application/json")
    resp.headers["Cache-Control"] = f"private, max-age={max_age}"
    return resp


def _json_response(payload, max_age: int = API_MAX_AGE) -> Response:
    """Encode and serve a payload that is not worth caching (portfolio views)."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return _bytes_response(raw, gzip.compress(raw, 6), max_age)


def _prefetch(fn, keys, workers: int = 8) -> None:
    """Warm a per-symbol cache concurrently.

    The portfolio views ask Yahoo for one instrument at a time, so their latency
    was the SUM of every quote and every price series. The callers below stay
    sequential and simply find the caches already populated — no change to the
    arithmetic, only to when the waiting happens.
    """
    keys = [k for k in dict.fromkeys(keys) if k]
    if len(keys) < 2:
        for k in keys:
            try:
                fn(k)
            except Exception:
                pass
        return
    with ThreadPoolExecutor(max_workers=min(workers, len(keys))) as ex:
        for fut in as_completed([ex.submit(fn, k) for k in keys]):
            try:
                fut.result()
            except Exception:
                pass                      # a failure is handled by the caller


def _epoch_ms(s):
    """Datetime Series -> epoch MILLISECONDS, whatever resolution pandas chose.

    `astype("int64")` returns the raw count in the column's OWN unit, and
    `fetch_daily` yields `datetime64[s, UTC]`, not `[ns]` — so a fixed `// 10**6`
    silently divided seconds by a million and produced 1083 instead of
    1083677400000. Normalize the unit first, then the conversion is exact.
    """
    return pd.to_datetime(s, utc=True).dt.as_unit("ms").astype("int64").to_numpy()


def _clean(x) -> float | None:
    """NaN/inf -> None so the JSON is valid."""
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _r(x, n: int) -> float | None:
    """Clean + round — trims the payload (fewer decimals = smaller JSON)."""
    v = _clean(x)
    return None if v is None else round(v, n)


def _build(symbol: str, vol_w: float, tf: str = "daily") -> dict:
    df = fetch_daily(symbol, years=YEARS)
    # El nombre se lee AQUÍ, del meta que vino con este mismo frame, y antes de
    # transformarlo: `attrs` no sobrevive garantizado a un resample.
    name = _name_from_meta(df.attrs.get("meta"))
    # Continuous futures report rollover-contaminated volume -> drop it so the
    # conviction layer falls back to volatility only for these.
    if symbol.upper().endswith("=F"):
        df = df.drop(columns=["volume"], errors="ignore")
    # Weekly re-scores the SAME asset on W-SUN bars with weekly-horizon windows;
    # daily is untouched (windows=None -> DAILY, byte-identical to before).
    if tf == "weekly":
        df = to_weekly(df)
        frame, s = analyze(df, vol_weight=vol_w, windows=WEEKLY)
    else:
        frame, s = analyze(df, vol_weight=vol_w)

    # Vectorized: `iterrows()` rebuilds a Series per row and dominated this
    # function (~400 ms of a 470 ms build). Pull columns once as numpy, round
    # once, and zip. Same output, same order.
    fin = frame["score"].notna().to_numpy()
    sub = frame.loc[fin]
    ts = _epoch_ms(sub["date"])
    def col(name, nd):
        return [None if not math.isfinite(v) else round(float(v), nd)
                for v in sub[name].to_numpy(dtype=float)]
    close, score = col("close", 2), col("score", 2)
    stretch, rsi = col("stretch", 1), col("rsi", 1)
    dd, td = col("drawdown", 1), col("trend_dev", 1)
    vol, climax = col("volatility", 1), col("climax", 0)
    zone = sub["zone_name"].tolist()
    series = [
        {"t": int(t), "close": c, "score": s, "zone": z, "stretch": st, "rsi": rs,
         "drawdown": d, "trend_dev": e, "volatility": v,
         "climax": x}     # client derives conviction from this
        for t, c, s, z, st, rs, d, e, v, x in
        zip(ts, close, score, zone, stretch, rsi, dd, td, vol, climax, strict=True)
    ]

    # De paso, y sin coste: la cartera preguntará por esta misma zona.
    # Sólo la diaria con el peso por defecto, que es la lectura canónica —
    # guardar aquí una semanal o una con el peso movido en el tuner haría que
    # la tabla de posiciones enseñase la zona de OTRO modelo.
    if tf == "daily" and abs(vol_w - VOL_W_DEFAULT) < 1e-9:
        _zone_put(symbol, s)

    return {
        "symbol": symbol,
        "name": name,
        "as_of": str(s.date.date()),
        "model": s.model,
        "vol_w": vol_w,
        "tf": tf,
        # Every point below is normalized against its own past only, so a past
        # date shows what the index said THAT day and never moves afterwards.
        "causal": True,
        "series": series,
        "summary": {
            "zone": s.zone_name,
            "score": _r(s.score, 2),
            "close": _r(s.close, 2),
            "dwell": s.dwell,
            "verdict": s.verdict,
            "date": str(s.date.date()),
            "stretch": _r(s.stretch, 1),
            "rsi": _r(s.rsi, 1),
            "drawdown": _r(s.drawdown, 1),
            "trend_dev": _r(s.trend_dev, 1),
            "volatility": _r(s.volatility, 1),
            "climax": _r(s.climax, 0),
            "vol_pct": _r(s.vol_pct, 0),
            "volu_pct": _r(s.volu_pct, 0),
        },
    }


# Sólo el veredicto de hoy, sin la serie. La cartera necesita la zona de diez
# instrumentos a la vez y decodificar diez payloads de 25 años para leer una
# palabra de cada uno cuesta más que volver a calcularla. `_build` la deja aquí
# de paso, así que un activo que ya se miró en el gráfico sale gratis.
_zone_cache: dict[str, tuple[float, dict]] = {}


def _zone_put(symbol: str, s) -> dict:
    z = {"zone": s.zone_name, "score": _r(s.score, 2), "dwell": s.dwell,
         "close": _r(s.close, 2), "model": s.model, "date": str(s.date.date())}
    _cache_put(_zone_cache, symbol.upper().strip(), (time.time(), z), cap=CACHE_MAX)
    return z


def _zone_of(symbol: str) -> dict:
    """Zona de HOY para un símbolo. Lanza lo que lance el motor."""
    sym = symbol.upper().strip()
    hit = _zone_cache.get(sym)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    safe_symbol(sym)
    df = fetch_daily(sym, years=YEARS)
    if sym.endswith("=F"):
        df = df.drop(columns=["volume"], errors="ignore")
    _frame, s = analyze(df)
    return _zone_put(sym, s)


def _get(symbol: str, vol_w: float = VOL_W_DEFAULT, tf: str = "daily",
         limited: bool = False):
    """Encoded zones payload. Weight AND timeframe are part of the cache identity
    so daily and weekly never collide."""
    symbol = symbol.upper().strip()
    return _encoded(f"zones|{symbol}|{vol_w:.3f}|{tf}",
                    lambda: _build(symbol, vol_w, tf), limited)


def _get_regime(symbol: str, light: bool, limited: bool = False):
    """Encoded regime payload, full or light.

    The two variants are separate cache entries so the light one — what the
    multi-asset pages fan out over — is a 2 KB lookup with no filtering work.
    The regime module's own dict cache holds the intermediate payload just long
    enough for the sibling variant to reuse it.
    """
    symbol = symbol.upper().strip()

    def build():
        p = regime_get(symbol)
        return {k: v for k, v in p.items() if k not in _HEAVY_KEYS} if light else p
    return _encoded(f"regime|{'L' if light else 'F'}|{symbol}", build, limited)


def _build_target(symbol: str, vol_w: float, tf: str = "daily") -> dict:
    """Target-price block: the price at which the index would READ each extreme
    zone, three ways. Kept on its own endpoint because the inversion re-runs the
    engine dozens of times (seconds on a 25-year history) and must never make the
    main chart wait for it. `target` is null when the history is too short.

    Weekly inverts the SAME engine on W-SUN bars with weekly windows, so the
    levels match the weekly chart; daily is untouched (windows=None -> DAILY)."""
    df = fetch_daily(symbol, years=YEARS)
    if symbol.upper().endswith("=F"):
        df = df.drop(columns=["volume"], errors="ignore")
    if tf == "weekly":
        df = to_weekly(df)
        target = _compute_target(df, symbol, vol_w, windows=WEEKLY)
    else:
        target = _compute_target(df, symbol, vol_w)
    return {"symbol": symbol, "vol_w": vol_w, "tf": tf, "target": target}


def _get_target(symbol: str, vol_w: float = VOL_W_DEFAULT, tf: str = "daily",
                limited: bool = False):
    symbol = symbol.upper().strip()
    return _encoded(f"target|{symbol}|{vol_w:.3f}|{tf}",
                    lambda: _build_target(symbol, vol_w, tf), limited)


@app.route("/")
def index():
    return render_template("index.html", curated=CURATED, default="NLR")


# ── Exchange crypto symbols -> Yahoo ──────────────────────────────────────
# Binance / Bitget / KuCoin write a pair glued together and quoted in a
# stablecoin — ETHUSDT, SOL-USDT, BTC/USDC. Yahoo quotes the same coin as
# BASE-USD (ETH-USD) and 404s on the exchange shape, which is why "ethusdt"
# returned nothing. Translate the pair to Yahoo's form. A deterministic strip
# beats Yahoo's own search here: its crypto ranking surfaces impostors first
# (searching "ondo" returns four tokenized-stock look-alikes before ONDO-USD).
# USDT is listed before USD so "ETHUSDT" strips the four-letter quote, not "USD".
_QUOTE_CCYS = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USDD", "DAI", "USD")


def _crypto_to_yahoo(symbol: str) -> str:
    """ETHUSDT / ETH-USDT / ETH/USDT / ETHUSD -> ETH-USD. Left untouched when the
    symbol is not an exchange crypto pair (stocks, ETFs, forex, ISIN lines)."""
    s = str(symbol or "").strip().upper()
    if not s or "=" in s or "." in s:          # forex (EURUSD=X) and ISIN lines
        return s
    core = s.replace("/", "-")
    for suf in ("-PERP", "-SWAP", "-SPOT", "PERP"):   # drop a perpetual/spot marker
        if core.endswith(suf):
            core = core[: -len(suf)]
            break
    if "-" in core:                            # BASE-QUOTE already split
        base, _, quote = core.rpartition("-")
        return f"{base}-USD" if base and quote in _QUOTE_CCYS else s
    for q in _QUOTE_CCYS:                       # glued form: ETHUSDT
        if core.endswith(q) and len(core) > len(q):
            return f"{core[:-len(q)]}-USD"
    return s


@app.route("/api/zones")
def api_zones():
    symbol = _crypto_to_yahoo(request.args.get("symbol", "NLR"))
    try:
        vol_w = float(request.args.get("vol_w", VOL_W_DEFAULT))
    except (TypeError, ValueError):
        vol_w = VOL_W_DEFAULT
    vol_w = max(0.0, min(0.40, vol_w))
    tf = request.args.get("tf", "daily")
    if tf not in ("daily", "weekly"):            # unknown -> daily, never trust input
        tf = "daily"
    # Validate BEFORE the rate limiter. A rejected symbol costs nothing to
    # answer, and every invalid one is a distinct cache key — so charging for
    # it would let garbage input drain the budget that legitimate lookups need.
    try:
        safe_symbol(symbol)
    except BadSymbol as e:
        return jsonify({"error": str(e)}), 400
    try:
        raw, gz = _get(symbol, vol_w, tf, limited=True)
    except TooBusy:
        return _busy()
    except BadSymbol as e:  # not a ticker shape -> the caller's mistake
        return jsonify({"error": str(e)}), 400
    except NoHistory as e:  # priceable but not chartable -> say exactly that
        return jsonify({"error": str(e)}), 422
    except Exception as e:  # bad ticker / Yahoo hiccup -> readable error
        return jsonify({"error": f"No pude cargar '{symbol}': {e}"}), 502
    return _bytes_response(raw, gz)


@app.route("/api/target")
def api_target():
    symbol = _crypto_to_yahoo(request.args.get("symbol", "NLR"))
    try:
        vol_w = float(request.args.get("vol_w", VOL_W_DEFAULT))
    except (TypeError, ValueError):
        vol_w = VOL_W_DEFAULT
    vol_w = max(0.0, min(0.40, vol_w))
    tf = request.args.get("tf", "daily")
    if tf not in ("daily", "weekly"):
        tf = "daily"
    try:
        safe_symbol(symbol)                       # validate before charging
    except BadSymbol as e:
        return jsonify({"error": str(e)}), 400
    try:
        raw, gz = _get_target(symbol, vol_w, tf, limited=True)
    except TooBusy:
        return _busy()
    except BadSymbol as e:
        return jsonify({"error": str(e)}), 400
    except NoHistory as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        return jsonify({"error": f"No pude calcular objetivo de '{symbol}': {e}"}), 502
    return _bytes_response(raw, gz)


@app.route("/regime")
def regime_page():
    return render_template("regime.html", curated=REGIME_CURATED, default="SPY")


@app.route("/screener")
def screener_page():
    return render_template("screener.html", curated=REGIME_CURATED)


@app.route("/comite")
def comite_page():
    return render_template("comite.html", curated=REGIME_CURATED)


@app.route("/validation")
def validation_report():
    """Serve the static P5 scientific validation report (generated offline by
    validation/run.py + report.py). Read-only document, not a dashboard."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation", "report.html")
    if not os.path.exists(path):
        return Response("Informe no generado aún. Ejecuta validation/run.py y validation/report.py.",
                        mimetype="text/plain"), 404
    with open(path, encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


# The per-date arrays are ~97% of a regime payload and the multi-asset pages
# (screener, comité) never read them: they fan out over the whole curated list,
# so shipping the full document 24 times moved ~11 MB to render a table.
_HEAVY_KEYS = ("series", "phase")


@app.route("/api/regime")
def api_regime():
    symbol = _crypto_to_yahoo(request.args.get("symbol", "SPY"))
    try:                                 # see api_zones: validate before charging
        safe_symbol(symbol)
    except BadSymbol as e:
        return jsonify({"error": str(e)}), 400
    try:
        raw, gz = _get_regime(symbol, request.args.get("view") == "light", limited=True)
    except TooBusy:
        return _busy()
    except Exception as e:
        return jsonify({"error": f"No pude cargar '{symbol}': {e}"}), 502
    return _bytes_response(raw, gz)


# ─────────────────────────────────────────────────────────────────────────
# Cartera — libro de movimientos (compras/ventas) con importación CSV/Excel.
# Almacenamiento propio en SQLite; no toca el motor ni el resto de paneles.
# ─────────────────────────────────────────────────────────────────────────
# The portfolio file is the ONLY per-person state, so it is also the whole
# isolation boundary: two instances that point at different files cannot see
# each other's book, with no per-request filtering to get wrong. Anchored to
# this directory when unset, which is where the original instance keeps it.
CARTERA_DB = os.environ.get("CARTERA_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cartera.db")
_price_cache: dict[str, tuple[float, float | None]] = {}


def _cartera_conn():
    c = sqlite3.connect(CARTERA_DB)
    # SQLite creates the file with 0644 minus umask, so on a shared host every
    # local account could read the portfolio. Tighten on each connect rather
    # than only at creation: it also repairs a file that already exists, and
    # covers the -wal/-shm sidecars SQLite makes on its own.
    for path in (CARTERA_DB, CARTERA_DB + "-wal", CARTERA_DB + "-shm"):
        try:
            if os.path.exists(path) and (os.stat(path).st_mode & 0o077):
                os.chmod(path, 0o600)
        except OSError:
            pass                          # not fatal: never block the request
    c.execute("""CREATE TABLE IF NOT EXISTS movements(
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, ticker TEXT, side TEXT,
        quantity REAL, price REAL, fee REAL, note TEXT,
        created TEXT DEFAULT CURRENT_TIMESTAMP)""")
    # migration: display name (funds have opaque 0P... symbols) + instrument type
    have = {r[1] for r in c.execute("PRAGMA table_info(movements)")}
    if "name" not in have:
        c.execute("ALTER TABLE movements ADD COLUMN name TEXT")
    if "kind" not in have:
        c.execute("ALTER TABLE movements ADD COLUMN kind TEXT")
    # El TER no lo publica el endpoint de cotizaciones que usa este panel, y no
    # se inventa: lo escribe quien tiene el folleto delante. Tabla aparte y no
    # una columna en `movements` porque es una propiedad del INSTRUMENTO, no de
    # la operación: si viviera en el movimiento, dos compras del mismo fondo
    # podrían declarar comisiones distintas y la cartera no sabría cuál creer.
    c.execute("""CREATE TABLE IF NOT EXISTS instrument_meta(
        ticker TEXT PRIMARY KEY, ter REAL)""")
    meta = {r[1] for r in c.execute("PRAGMA table_info(instrument_meta)")}
    if "target" not in meta:
        c.execute("ALTER TABLE instrument_meta ADD COLUMN target REAL")
    # De dónde salió el TER y cuándo. Un número sin procedencia envejece sin
    # avisar: dentro de dos años nadie sabrá si sigue vigente ni de qué ficha
    # se copió, y el coste anual de la cartera se calculará sobre un dato que
    # puede llevar mucho tiempo siendo falso.
    if "ter_source" not in meta:
        c.execute("ALTER TABLE instrument_meta ADD COLUMN ter_source TEXT")
    if "ter_date" not in meta:
        c.execute("ALTER TABLE instrument_meta ADD COLUMN ter_date TEXT")
    # El plan de quien invierte: una fila, porque es una cartera. `id=1` fijo y
    # no autoincremental — un plan nuevo SUSTITUYE al anterior, y una tabla que
    # acumula planes viejos obliga a decidir cuál vale cada vez que se lee.
    # Splits ya resueltos. Sin esto no hay forma de dejar de avisar: el programa
    # NO puede saber si una cantidad del libro está en la escala vieja o en la
    # nueva —«10 títulos» es el mismo número a los dos lados—, así que la única
    # manera de cerrar el aviso es que alguien lo cierre.
    c.execute("""CREATE TABLE IF NOT EXISTS split_ack(
        ticker TEXT, split_date TEXT, action TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ticker, split_date))""")
    c.execute("""CREATE TABLE IF NOT EXISTS portfolio_goal(
        id INTEGER PRIMARY KEY CHECK (id = 1),
        capital REAL, horizon_years REAL, monthly REAL)""")
    goal_cols = {r[1] for r in c.execute("PRAGMA table_info(portfolio_goal)")}
    # Minusvalías pendientes de compensar de ejercicios anteriores. Se guardan
    # porque el simulador fiscal las necesita en CADA simulación, y volver a
    # teclearlas cada vez garantiza que un día se teclee otra cifra distinta.
    if "pending_losses" not in goal_cols:
        c.execute("ALTER TABLE portfolio_goal ADD COLUMN pending_losses REAL")
    return c


def _portfolio_goal():
    """El plan declarado, o None. Nunca un plan a cero: no haber decidido un
    objetivo y haberse puesto uno de cero euros no son lo mismo."""
    with _cartera_conn() as c:
        row = c.execute("SELECT capital, horizon_years, monthly, pending_losses "
                        "FROM portfolio_goal WHERE id=1").fetchone()
    if not row or all(v is None for v in row):
        return None
    return {"capital": row[0], "horizon_years": row[1], "monthly": row[2],
            "pending_losses": row[3]}


# ── Búsqueda de instrumentos (ETFs, fondos, acciones) vía Yahoo Finance ──
# El buscador de Yahoo indexa ETFs y fondos europeos y acepta ISIN o nombre;
# devuelve un símbolo que el resto del sistema (precios, gráfica) ya sabe valorar.
_search_cache: dict[str, tuple[float, list]] = {}
_KIND_ES = {"EQUITY": "Acción", "ETF": "ETF", "MUTUALFUND": "Fondo", "INDEX": "Índice",
            "CRYPTOCURRENCY": "Cripto", "CURRENCY": "Divisa", "FUTURE": "Futuro"}


def _yahoo_search(q: str, limit: int = 12) -> list:
    q = (q or "").strip()
    if len(q) < 2:
        return []
    key = q.lower()
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        url = ("https://query2.finance.yahoo.com/v1/finance/search?q="
               + urllib.parse.quote(q)
               + f"&quotesCount={int(limit)}&newsCount=0&lang=es-ES&region=ES")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            quotes = json.load(r).get("quotes", [])
    except Exception:
        return []                                 # transient failure -> do NOT cache
    out = []
    for x in quotes:
        sym = x.get("symbol")
        qt = x.get("quoteType")
        if not sym or qt not in _KIND_ES:
            continue
        out.append({"symbol": sym, "kind": _instrument_kind(sym, _KIND_ES[qt]),
                    "name": x.get("shortname") or x.get("longname") or sym,
                    "exchange": x.get("exchDisp", "")})
    _cache_put(_search_cache, key, (time.time(), out), cap=256)
    return out


def _resolve_symbol(query: str):
    """ISIN or free text -> (symbol, name, kind). Falls back to the raw query."""
    q = str(query or "").strip()
    if not q:
        return "", "", ""
    if _looks_like_isin(q) or " " in q:
        res = _yahoo_search(q)
        # An ISIN names ONE instrument, so the listing whose symbol is built from
        # that ISIN is that instrument — taken before any preference by type.
        # Searching IE000M7V94E1 also returns NUCL.SW: another currency, another
        # share class of the same strategy. Preferring it by type would book the
        # holding under a second ticker and split the position in two.
        if _looks_like_isin(q):
            want = q.upper()
            for r in res:
                if _symbol_isin(r["symbol"]) == want:
                    return r["symbol"], r["name"], r["kind"]
        # prefer ETF/fund/equity over indices; keep first otherwise
        for pref in ("ETF", "Fondo", "Acción"):
            for r in res:
                if r["kind"] == pref:
                    return r["symbol"], r["name"], r["kind"]
        if res:
            return res[0]["symbol"], res[0]["name"], res[0]["kind"]
    return q.upper(), "", ""


_meta_cache: dict[str, tuple[float, tuple]] = {}

# ── History proxies ───────────────────────────────────────────────────────
# Some listings are priceable but not chartable: Yahoo answers with `meta` (so
# a live quote exists) and no `timestamp` array at all. Both of these are the
# Stuttgart line of an instrument that also trades elsewhere, and the sibling
# listing DOES carry history — same ISIN, same currency, arbitraged against
# each other, so it is the same asset read through another window rather than
# an approximation.
#
# Deliberately a hand-checked table, NOT ISIN auto-discovery. Yahoo lists
# `GOLD.PA` under the same ISIN as `GOLD.MI` with MORE history — and it is a
# different share class quoting ~half the price. Anything automatic would have
# picked it on bar count and halved the charted portfolio in silence.
#
# Used ONLY for the historical series. Live valuation always uses the real
# ticker, so today's number is never a proxy's.
HISTORY_PROXY = {
    "IE000M7V94E1.SG": "NUKL.DE",   # VanEck Uranium & Nuclear UCITS, Xetra, EUR
    "FR0013416716.SG": "GOLD.MI",   # Amundi Physical Gold ETC, Milan, EUR
}
PROXY_MAX_DEV = 0.02            # a sibling further than 2% away is not the same line


# Full instrument name for the header. Yahoo answers "Tesla, Inc." / "Amazon.com,
# Inc."; the panel wants the recognizable company ("TESLA" / "AMAZON"), so a short
# tail of legal-form tokens is dropped and the rest upper-cased. Curated symbols
# keep their hand-written name client-side, so this only ever labels searched
# tickers — an over-eager strip on some odd ETF name is cosmetic, never wrong data.
def _quote_meta(symbol: str):
    """(live price, currency) from Yahoo chart meta. Cached ~120s (near real-time)."""
    t = symbol.upper().strip()
    hit = _meta_cache.get(t)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    try:
        # Same validation as fetch_daily: encoding alone would stop the URL
        # being steered, but rejecting a bad shape avoids the pointless
        # upstream call and gives the caller a real reason.
        u = ("https://query1.finance.yahoo.com/v8/finance/chart/"
             + safe_symbol(t) + "?range=1d&interval=1d")
        m = json.load(urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=12)
        )["chart"]["result"][0]["meta"]
        res = (m.get("regularMarketPrice"), m.get("currency"))
    except Exception:
        return (None, None)                       # transient -> do not cache
    _cache_put(_meta_cache, t, (time.time(), res), cap=256)
    return res


def _last_price(ticker: str):
    """Live native price (chart meta), falling back to the last daily close.

    A failure is NOT cached: pinning `None` for the whole TTL turns one bad
    second into ten minutes of an unvalued position.
    """
    p, _ = _quote_meta(ticker)
    if p is not None:
        return float(p)
    t = ticker.upper().strip()
    hit = _price_cache.get(t)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        px = float(fetch_daily(t, years=1)["close"].iloc[-1])
    except Exception:
        return None                               # transient -> retry next call
    _cache_put(_price_cache, t, (time.time(), px), cap=256)
    return px


def _instrument_ccy(symbol: str):
    """Quote currency, or None if Yahoo did not say.

    Returning BASE_CCY on failure is the expensive kind of wrong: an unknown
    currency would be treated as already-EUR and skip conversion entirely,
    silently reporting a USD total under a EUR label. Unknown must stay unknown
    so the caller can refuse to value the position.
    """
    _, c = _quote_meta(symbol)
    return c or None


def _ccy_base_factor(ccy: str):
    """(base ISO currency, multiplier) — handles pence-quoted instruments (GBp, GBX...)."""
    c = (ccy or "").strip()
    if c in ("GBp", "GBX"):
        return "GBP", 0.01
    if c == "ZAc":
        return "ZAR", 0.01
    if c == "ILA":
        return "ILS", 0.01
    return (c or BASE_CCY), 1.0


def _fx_now(ccy):
    """EUR per 1 unit of the quoted currency, live. None when NOT KNOWN.

    Callers must treat None as "cannot value this", never as 1.0 — an
    unavailable rate coerced to 1.0 prices a dollar as a euro in silence.
    """
    if not ccy:
        return None
    base, f = _ccy_base_factor(ccy)
    if base == BASE_CCY:
        return f
    p, _ = _quote_meta(f"{base}EUR=X")
    return (p * f) if p else None


def _fx_series_eur(ccy):
    """Daily EUR-per-quoted-unit series for historical conversion.

    None = identity (the instrument is already quoted in EUR). Unknown
    currencies never reach here: callers drop those positions first.
    """
    base, f = _ccy_base_factor(ccy)
    if base == BASE_CCY:
        return None
    s = _close_series(f"{base}EUR=X")
    return None if s is None else (s * f)


def _parse_upload(filename: str, data: bytes):
    name = filename.lower()
    # `nrows` bounds the frame BEFORE the per-row loop: reading a capped frame
    # is what keeps a hostile file from turning into minutes of Python.
    cap = MAX_UPLOAD_ROWS + 1
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data), nrows=cap)
    else:
        df = pd.read_csv(io.BytesIO(data), sep=_sniff_sep(data), engine="c",
                         encoding="utf-8-sig", nrows=cap)
    truncated = len(df) > MAX_UPLOAD_ROWS
    if truncated:
        df = df.iloc[:MAX_UPLOAD_ROWS]
    cols = {}
    # pass 1: exact column-name match (safest, resolves cantidad/nombre/nota cleanly)
    for c in df.columns:
        nc = _norm_col(c)
        for field, syn in COLSYN.items():
            if field not in cols and nc in syn:
                cols[field] = c
                break
    # pass 2: prefix match, but only with tokens >=4 chars so short synonyms
    # (e.g. quantity's "num") can't hijack columns like "nombre"/"nota"
    used = set(cols.values())
    for c in df.columns:
        if c in used:
            continue
        nc = _norm_col(c)
        for field, syn in COLSYN.items():
            if field not in cols and any(len(s) >= 4 and nc.startswith(s) for s in syn):
                cols[field] = c
                used.add(c)
                break
    rows, errors = [], []
    # Each distinct ISIN costs one outbound Yahoo search, so a crafted file is
    # a request amplifier pointed at a third party. Resolutions are memoised
    # per file and capped; past the cap the raw ISIN is kept as the ticker.
    resolved: dict[str, tuple] = {}
    for i, row in df.iterrows():
        try:
            raw = str(row[cols["ticker"]]).strip() if "ticker" in cols else ""
            if not raw or raw.upper() == "NAN":
                continue
            nm = (str(row[cols["name"]]).strip() if "name" in cols and pd.notna(row[cols["name"]]) else "")
            tk, kind = raw.upper(), ""
            if _looks_like_isin(raw):                 # ISIN column -> resolve to a Yahoo symbol
                if raw in resolved:
                    sym, rn, kd = resolved[raw]
                elif len(resolved) < MAX_ISIN_LOOKUPS:
                    sym, rn, kd = resolved.setdefault(raw, _resolve_symbol(raw))
                else:
                    sym, rn, kd = "", "", ""
                if sym:
                    tk, kind, nm = sym, kd, (nm or rn)
            kind = _instrument_kind(tk, kind)          # the symbol decides the type
            qty = _num(row[cols["quantity"]]) if "quantity" in cols else None
            price = _num(row[cols["price"]]) if "price" in cols else None
            fee = (_num(row[cols["fee"]]) if "fee" in cols else 0.0) or 0.0
            side = _norm_side(row[cols["side"]], qty) if "side" in cols else ("sell" if (qty or 0) < 0 else "buy")
            qty = abs(qty) if qty is not None else None
            date = _norm_date(row[cols["date"]]) if "date" in cols else ""
            note = (str(row[cols["note"]]).strip() if "note" in cols and pd.notna(row[cols["note"]]) else "")
            if qty is None or price is None:
                errors.append(f"fila {int(i) + 2}: falta cantidad o precio")
                continue
            rows.append({"date": date, "ticker": tk, "side": side, "quantity": qty,
                         "price": price, "fee": fee, "note": note, "name": nm, "kind": kind})
        except Exception as e:
            errors.append(f"fila {int(i) + 2}: {e}")
    if truncated:
        errors.append(f"el archivo excede {MAX_UPLOAD_ROWS} filas: solo se han "
                      f"leído las primeras {MAX_UPLOAD_ROWS}")
    return rows, errors, list(cols.keys())


class _Market:
    """El puerto de datos de mercado que pide `cartera.positions`.

    Cada método resuelve el nombre del módulo EN LA LLAMADA, no al construirse.
    Es deliberado: los tests de la aritmética sustituyen `_fx_now`,
    `_last_price` o `_instrument_ccy` en este módulo para fijar el mercado y
    dejar variar sólo la contabilidad, y un enlace capturado en `__init__` los
    dejaría fuera sin que nada fallase.
    """

    def warm(self, tickers):
        _prefetch(_quote_meta, tickers)               # quote + currency, in parallel
        # Sólo las divisas que NO son la base necesitan serie histórica: un
        # instrumento en euros no se convierte, y "EUREUR=X" es un viaje de ida
        # y vuelta a ninguna parte.
        cur = {_instrument_ccy(t) for t in tickers}
        bases = {_ccy_base_factor(c)[0] for c in cur if c}
        _prefetch(_close_series, [b + "EUR=X" for b in bases if b != BASE_CCY])

    def currency(self, ticker):
        return _instrument_ccy(ticker)

    def base_factor(self, ccy):
        return _ccy_base_factor(ccy)

    def fx_series(self, ccy):
        return _fx_series_eur(ccy)

    def fx_now(self, ccy):
        return _fx_now(ccy)

    def last_price(self, ticker):
        return _last_price(ticker)


def _positions(movs):
    """Posiciones valoradas. La aritmética vive en `cartera.positions`; aquí
    sólo queda el cable que le enchufa este panel como fuente de mercado."""
    return _compute_positions(movs, _Market())


def _cartera_payload():
    with _cartera_conn() as c:
        cur = c.execute("SELECT id,date,ticker,name,kind,side,quantity,price,fee,note FROM movements ORDER BY date DESC, id DESC")
        cols = [d[0] for d in cur.description]
        movs = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    # Normalise on READ as well as on write: rows stored before the classifier
    # existed keep whatever Yahoo said that day, and `_positions` takes the type
    # from the most recent movement — so a single mislabelled entry re-badges the
    # whole position. Derived from the symbol, the two lists cannot disagree.
    for m in movs:
        m["kind"] = _instrument_kind(m.get("ticker"), m.get("kind"))
    positions = _positions(movs)
    with _cartera_conn() as c:
        meta = {t: {"ter": ter, "target": tgt, "ter_source": src_, "ter_date": fecha}
                for t, ter, tgt, src_, fecha in c.execute(
                    "SELECT ticker, ter, target, ter_source, ter_date FROM instrument_meta")}
    for p in positions:
        m = meta.get(p["ticker"]) or {}
        p["ter"] = m.get("ter")
        p["ter_source"] = m.get("ter_source")
        p["ter_date"] = m.get("ter_date")
        p["target"] = m.get("target")
        # Cuánto ha aportado ESTA posición al resultado, en euros. No es el
        # peso ni el porcentaje de subida: una posición del 5% que se dobló ha
        # hecho más dinero que una del 40% que subió un 2%, y eso no se deducía
        # de ninguna de las columnas que ya había.
        piezas = [p.get("unreal"), p.get("realized"), p.get("income")]
        p["contribution"] = (round(sum(x for x in piezas if x is not None), 2)
                             if any(x is not None for x in piezas) else None)
        # Coste anual que se lleva la gestora del valor de HOY. Es el único
        # coste que no se ve nunca en un extracto: no se cobra, se descuenta del
        # valor liquidativo. Por eso es el que hay que escribir en una pantalla.
        p["ter_year"] = (round(p["market_value"] * p["ter"] / 100, 2)
                         if (p["ter"] and p.get("market_value")) else None)
    open_pos = [p for p in positions if p["qty"] > 1e-9]
    # invested / market_value / unreal are summed over the SAME set — the
    # positions that could actually be valued. Mixing a partial numerator with
    # a full denominator understates the return and reads as a loss.
    valued = [p for p in open_pos if p["valued"]]
    invested = sum(p["invested"] for p in valued)
    mval = sum(p["market_value"] for p in valued)
    unreal = sum(p["unreal"] for p in valued)
    realized = sum(p["realized"] for p in positions if p["realized"] is not None)
    realized_fifo = sum(p["realized_fifo"] for p in positions if p["realized_fifo"] is not None)
    fees = sum(p["fees"] for p in positions)
    withheld = sum(p["withheld"] for p in positions)
    n_ops = sum(p["n_ops"] for p in positions)
    ter_year = sum(p["ter_year"] for p in positions if p.get("ter_year") is not None)
    # Sobre qué parte del dinero se conoce el TER. Sin esto, un 0,12% anual
    # calculado sobre un tercio de la cartera se lee como el coste de la
    # cartera entera, que es la lectura tranquilizadora y falsa.
    ter_cov = sum(p["market_value"] for p in positions
                  if p.get("ter") is not None and p.get("market_value"))
    income = sum(p["income"] for p in positions if p["income"] is not None)
    currencies = sorted({p["ccy"] for p in open_pos if p.get("ccy") and p["ccy"] != "?"})
    unvalued = [{"ticker": p["ticker"], "why": p["why"]} for p in open_pos if not p["valued"]]
    oversold = [{"ticker": p["ticker"], "qty": p["oversold"]}
                for p in positions if p["oversold"]]
    summary = {"n_movements": len(movs), "n_positions": len(open_pos), "base": BASE_CCY,
               "currencies": currencies,
               "invested": round(invested, 2), "market_value": round(mval, 2),
               "unreal": round(unreal, 2),
               "unreal_pct": round(unreal / invested * 100, 2) if invested > 1e-9 else None,
               "realized": round(realized, 2),
               # El mismo resultado con el otro criterio de coste. Coinciden
               # salvo que haya ventas PARCIALES; cuando divergen, la diferencia
               # es exactamente lo que separa la lectura de la cartera de la
               # declaración, y esconderla no la hace desaparecer.
               "realized_fifo": round(realized_fifo, 2),
               # Renta cobrada, aparte de las plusvalías: ni suma al realizado
               # ni baja el coste.
               "income": round(income, 2),
               "n_dividends": sum(1 for m in movs if m.get("side") == "div"),
               # Lo que cuesta tener esta cartera, que estaba guardado y sin
               # sumar en ningún sitio.
               "fees": round(fees, 2), "withheld": round(withheld, 2),
               "n_ops": n_ops,
               "ter_year": round(ter_year, 2),
               "ter_coverage": (round(ter_cov / mval * 100, 1) if mval > 1e-9 else 0.0),
               "ter_pct": (round(ter_year / ter_cov * 100, 3) if ter_cov > 1e-9 else None),
               # Rentabilidad total = lo que la posición aún no ha soltado + lo
               # que ya soltó + lo que pagó por el camino. Es la única de las
               # tres cifras que responde «¿cuánto he ganado?».
               "total_return": round(unreal + realized + income, 2),
               # What the totals above do NOT include, and why.
               "n_valued": len(valued), "unvalued": unvalued,
               "oversold": oversold,
               "n_undated": sum(1 for m in movs if not m.get("date"))}
    return {"movements": movs, "positions": positions, "summary": summary}


@app.route("/cartera")
def cartera_page():
    return render_template("cartera.html")


@app.route("/api/cartera", methods=["GET"])
def api_cartera_get():
    # Portfolio data changes only when the user edits it, so it is gzipped but
    # NOT browser-cached: a stale total after adding a movement is worse than
    # the round trip it would save.
    return _json_response(_cartera_payload(), max_age=0)


@app.route("/api/cartera", methods=["POST"])
def api_cartera_add():
    d = request.get_json(force=True, silent=True) or {}
    raw = str(d.get("ticker", "")).strip()
    qty, price = _num(d.get("quantity")), _num(d.get("price"))
    side = _norm_side(d.get("side", "buy"), qty)
    # Un dividendo llega como IMPORTE. El extracto de un banco da el total
    # cobrado y muchas veces ni menciona cuántos títulos lo generaron, así que
    # exigir la cantidad obligaría a inventársela. Sin ella, una unidad al
    # precio del importe: `cantidad x precio` sigue siendo el bruto, que es lo
    # único que la aritmética de posiciones lee.
    if side == "div" and qty is None:
        qty = 1.0
    if not raw or qty is None or price is None:
        return jsonify({"error": "instrumento, cantidad y precio son obligatorios"}), 400
    name = str(d.get("name", "")).strip()
    kind = str(d.get("kind", "")).strip()
    tk = raw.upper()
    if _looks_like_isin(raw) and not d.get("symbol"):    # entered an ISIN -> resolve
        sym, rn, kd = _resolve_symbol(raw)
        if sym:
            tk, kind, name = sym, (kind or kd), (name or rn)
    kind = _instrument_kind(tk, kind)                     # the symbol decides the type
    with _cartera_conn() as c:
        c.execute("INSERT INTO movements(date,ticker,name,kind,side,quantity,price,fee,note) VALUES(?,?,?,?,?,?,?,?,?)",
                  (_norm_date(d.get("date")) if d.get("date") else "", tk, name, kind, side, abs(qty), price,
                   _num(d.get("fee")) or 0.0, str(d.get("note", "")).strip()))
    _seed_geo_async(_geo_unknown([tk]))
    return jsonify(_cartera_payload())


@app.route("/api/cartera/upload", methods=["POST"])
def api_cartera_upload():
    """Import movements from CSV/Excel.

    Exact duplicates of rows ALREADY STORED are skipped by default. Re-importing
    the same file is the easy mistake and it silently doubles every position;
    losing a genuine same-day repeat purchase is the rare one and it is visible.
    The skipped rows are listed, and `?duplicates=allow` forces them in.
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no se recibió ningún archivo"}), 400
    try:
        rows, errors, detected = _parse_upload(f.filename, f.read())
    except Exception as e:
        return jsonify({"error": f"no pude leer el archivo: {e}"}), 400
    if not rows:
        return jsonify({"error": "no encontré filas válidas. Columnas detectadas: "
                                 + (", ".join(detected) or "ninguna"), "errors": errors[:10]}), 400

    allow_dupes = request.args.get("duplicates") == "allow"
    with _cartera_conn() as c:
        seen = {}
        for r in c.execute("SELECT date,ticker,side,quantity,price FROM movements"):
            k = _mov_key(dict(zip(("date", "ticker", "side", "quantity", "price"), r,
                                  strict=True)))
            seen[k] = seen.get(k, 0) + 1
        fresh, dupes = [], []
        for r in rows:
            k = _mov_key(r)
            if not allow_dupes and seen.get(k, 0) > 0:
                seen[k] -= 1                  # one stored row absorbs one incoming row
                dupes.append(r)
                continue
            fresh.append(r)
        if fresh:
            c.executemany("INSERT INTO movements(date,ticker,name,kind,side,quantity,price,fee,note) VALUES(?,?,?,?,?,?,?,?,?)",
                          [(r["date"], r["ticker"], r.get("name", ""), r.get("kind", ""), r["side"],
                            r["quantity"], r["price"], r["fee"], r["note"]) for r in fresh])
    _seed_geo_async(_geo_unknown(r["ticker"] for r in fresh))
    p = _cartera_payload()
    p["import"] = {"added": len(fresh), "detected": detected,
                   "errors": errors[:10], "n_errors": len(errors),
                   "skipped_duplicates": len(dupes),
                   "duplicates": [{"date": r["date"], "ticker": r["ticker"],
                                   "quantity": r["quantity"], "price": r["price"]}
                                  for r in dupes[:10]]}
    return jsonify(p)


@app.route("/api/cartera/export")
def api_cartera_export():
    """The movement book as a CSV the importer accepts unchanged.

    `kind` is deliberately NOT exported. The classifier derives it from the
    symbol on every read and write precisely so a stale label cannot re-badge a
    position; shipping it in the file would build the round trip that rule
    exists to prevent.

    Sorted oldest-first (the screen sorts newest-first): a file a human reads is
    a statement, and a statement runs forward in time.
    """
    with _cartera_conn() as c:
        rows = c.execute("SELECT date,ticker,name,side,quantity,price,fee,note "
                         "FROM movements ORDER BY date, id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CARTERA_EXPORT_COLS)
    for date, ticker, name, side, qty, price, fee, note in rows:
        # The words, not the codes: "compra"/"venta"/"dividendo" survive a human
        # opening the file in Excel and re-saving it, and `_norm_side` reads them
        # as whole words. A bare "c"/"v"/"d" is one careless edit away from
        # inverting a trade or turning income into a sale.
        w.writerow([date or "", ticker or "", name or "", _side_es(side),
                    _csv_num(qty), _csv_num(price), _csv_num(fee), note or ""])
    # utf-8-sig: without the BOM Excel reads a UTF-8 CSV as latin-1 and turns
    # every accented name into mojibake. The importer already opens with
    # `encoding="utf-8-sig"`, so the BOM costs the round trip nothing.
    raw = buf.getvalue().encode("utf-8-sig")
    resp = Response(raw, mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="cartera_{time.strftime("%Y-%m-%d")}.csv"')
    # `no-store`, not merely `max-age=0`: this is the portfolio itself, and a
    # proxy or a browser cache holding a copy of it on disk is the exact
    # outcome the host guard and the 0600 chmod are built to prevent.
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _cartera_returns(benchmark: str = "SPY", rango=None, desde=None, hasta=None):
    """Rentabilidad de la cartera: TWR, TIR y desglose por año.

    Se calcula sobre la reconstrucción a resolución DIARIA, no sobre los puntos
    submuestreados del gráfico: encadenar tramos de uno de cada cuatro días
    coloca los flujos en el día que no es.

    DOS TWR, y no es redundancia. El de arriba incluye los dividendos, que es
    la rentabilidad de verdad. El segundo los deja fuera, y existe SÓLO para
    comparar con el índice: `zones/data.py` pide el cierre crudo de Yahoo, no el
    ajustado, así que la serie del benchmark tampoco lleva sus propios
    dividendos. Comparar un total return contra un price return regala al que
    mira la rentabilidad por dividendo del índice —cerca de un 1,5% anual en un
    S&P 500— y la comparación deja de significar nada.
    """
    r = _reconstruct_portfolio(benchmark)
    if r.get("empty"):
        return {"twr": None, "tir": None, "empty": True,
                "benchmark_ticker": benchmark, "base": BASE_CCY}

    i0, i1 = _resolver_ventana(r["idx"], rango, desde, hasta)
    if i1 - i0 < 2:
        i0, i1 = 0, len(r["idx"]) - 1
        rango = "all"
    idx = r["idx"][i0:i1 + 1]
    port = r["port"][i0:i1 + 1]
    fechas = list(idx)
    # Convenio de `cartera.returns`: compra +, venta -, dividendo -. `flows` ya
    # trae compras en positivo y ventas en negativo; el dividendo se resta.
    con_div = [float(f - d) for f, d in zip(r["flows"][i0:i1 + 1],
                                            r["divs"][i0:i1 + 1], strict=True)]
    sin_div = [float(f) for f in r["flows"][i0:i1 + 1]]
    vals = [float(v) for v in port]

    t_total = _twr(vals, con_div, fechas)
    t_precio = _twr(vals, sin_div, fechas)

    # TIR: convenio de caja de quien invierte. Sale de su bolsillo = negativo.
    # En una ventana, el capital que ya había el primer día se trata como una
    # compra de ese día: es lo que "costó" tener la cartera al abrir el tramo.
    # Sin eso, un rango de tres meses tendría rentabilidad infinita, porque
    # habría cobros sin ninguna salida que los pagara.
    cf = []
    if i0 > 0 and vals and vals[0] > 1e-9:
        cf.append((fechas[0].date(), -vals[0]))
    arranque = 1 if i0 > 0 else 0
    for k in range(arranque, len(fechas)):
        neto = -float(r["flows"][i0 + k]) + float(r["divs"][i0 + k])
        if abs(neto) > 1e-9:
            cf.append((fechas[k].date(), neto))
    if vals and vals[-1] > 1e-9:
        cf.append((fechas[-1].date(), vals[-1]))   # el valor de hoy, como cobro final
    tir = _xirr(cf)

    # Caída máxima sobre el ÍNDICE DE RENDIMIENTO, nunca sobre los euros: el
    # valor en euros sube cuando se aporta, y aportar no es recuperarse.
    nav = _nav_series(vals, con_div)
    caida = _drawdown(nav, fechas)
    vol = _volatility(nav)

    bench = beta_val = corr_b = n_b = None
    if r["bench_val"] is not None:
        # El índice se compara sobre el MISMO tramo, resembrado igual que en el
        # gráfico: si no, tres meses de cartera irían contra una posición del
        # índice abierta hace años.
        _i, _p, _inv, bval = _rebasar(r, i0, i1)
        if bval is not None:
            bench = _twr([float(v) for v in bval], sin_div, fechas)
            # La beta va sobre ÍNDICES DE RENDIMIENTO, los dos construidos con
            # los mismos flujos: sobre la serie de euros, el salto del día de
            # una aportación entraría como movimiento de mercado y la inflaría.
            beta_val, corr_b, n_b = _beta(nav, _nav_series([float(v) for v in bval],
                                                           sin_div))

    aportado = float(sum(f for f in r["flows"][i0:i1 + 1] if f > 0))
    retirado = float(-sum(f for f in r["flows"][i0:i1 + 1] if f < 0))
    dividendos = float(sum(r["divs"][i0:i1 + 1]))
    dias = t_total.get("days") or 0
    anual = t_total.get("annualized")
    return {
        "empty": False,
        "twr": t_total, "twr_price_only": t_precio, "tir": tir,
        "drawdown": caida, "volatility": vol,
        # El tipo sin riesgo va EXPLÍCITO: un Sharpe sin decir contra qué se
        # calcula no se puede comparar con ningún otro, y el cero por defecto
        # de casi todas las pantallas no está escrito en ninguna parte.
        "sharpe": _sharpe(anual, vol, 0.0), "risk_free": 0.0,
        "range": (rango or "all"), "rebased": i0 > 0,
        "from": str(idx[0])[:10], "to": str(idx[-1])[:10],
        # La TIR ya es una tasa ANUAL, así que por debajo de un año es la misma
        # extrapolación que `annualize` se niega a hacer. Una sola bandera para
        # las dos cifras, para que no puedan discrepar.
        "annualizable": dias >= 365,
        "benchmark_ticker": benchmark, "benchmark_twr": bench,
        # Beta y correlación SIEMPRE juntas: una beta de 1,2 con correlación 0,3
        # no dice «se mueve un 20% más», dice que el índice explica muy poco de
        # lo que hace esta cartera y que ese 1,2 es casi ruido.
        "beta": (round(beta_val, 3) if beta_val is not None else None),
        "beta_corr": (round(corr_b, 3) if corr_b is not None else None),
        "beta_obs": n_b,
        "flows": {"aportado": round(aportado, 2), "retirado": round(retirado, 2),
                  "dividendos": round(dividendos, 2),
                  "valor_hoy": round(vals[-1], 2) if vals else 0.0},
        "excluded": r["excluded"], "covered": not r["excluded"],
        "base": BASE_CCY,
    }


# Ventana de la correlación. Un año de sesiones: suficiente para que la
# estimación no sea ruido y corto para que describa la cartera de HOY. Con diez
# años, dos activos que hace tiempo no se parecen salen correlacionados por lo
# que hicieron en 2020.
CORR_DAYS = 252
CORR_MIN_OBS = 60


def _cartera_correlacion(days: int = CORR_DAYS, benchmark: str = "SPY", payload=None):
    """Correlación entre las posiciones abiertas y diversificación REAL.

    El «N efectivo» que enseñaba el comité sale sólo de los pesos: cuenta dos
    ETFs correlacionados al 0,95 como dos apuestas distintas cuando son una.
    El propio comité ya lo advertía por escrito y no lo medía.

        N efectivo (pesos)        1 / SUM(w_i^2)
        N efectivo (correlación)  1 / SUM_ij(w_i w_j rho_ij)

    La segunda generaliza la primera: con correlaciones cero devuelve
    exactamente la primera, y con todo correlacionado a 1 devuelve 1, que es la
    verdad —una sola apuesta repartida en varias líneas.

    Se calcula sobre rendimientos EN EUROS, no en divisa nativa: dos activos
    que no se parecen en nada pueden moverse juntos para quien mide en euros
    simplemente porque los dos cotizan en dólares.
    """
    # El payload se acepta por parámetro: `/api/cartera/estado` ya lo tiene
    # calculado y sin esto lo rehacía tres veces —una por sí mismo, otra aquí y
    # otra en los splits— en la misma petición.
    payload = payload or _cartera_payload()
    abiertas = [p for p in payload["positions"]
                if p["qty"] > 1e-9 and p.get("market_value")]
    if len(abiertas) < 2:
        return {"n": len(abiertas), "matrix": [], "tickers": [],
                "eff_n_weights": (1.0 if abiertas else 0.0),
                "eff_n_corr": (1.0 if abiertas else 0.0),
                "excluded": [], "obs": 0, "days": days}

    series, excluidas = {}, []
    for p in abiertas:
        t = p["ticker"]
        s = _close_series(t)
        if s is None or not len(s):
            excluidas.append({"ticker": t, "why": "sin histórico"})
            continue
        cu = p.get("ccy")
        if not cu or cu == "?":
            excluidas.append({"ticker": t, "why": "sin divisa"})
            continue
        base, f = _ccy_base_factor(cu)
        if base == BASE_CCY:
            eur = s * f
        else:
            fx = _fx_series_eur(cu)
            if fx is None or not len(fx):
                excluidas.append({"ticker": t, "why": "sin tipo de cambio"})
                continue
            eur = s * fx.reindex(s.index.union(fx.index)).sort_index().ffill().reindex(s.index)
        # El índice se NORMALIZA a fecha antes de guardarlo. `_close_series`
        # conserva la hora de la barra, y esa hora no es la misma para todos:
        # un ETF de Nueva York indexa a las 13:30 UTC y un fondo europeo
        # valorado a NAV, a las 06:00. Intersecar marcas de tiempo daba CERO
        # sesiones en común entre ellos donde por fecha hay más de dos mil, así
        # que esta matriz se negaba a publicarse en cuanto la cartera mezclaba
        # una plaza americana con una europea — y decía «faltan sesiones», que
        # suena a poca historia y era otra cosa.
        series[t] = eur.dropna()
        series[t].index = series[t].index.normalize()
        series[t] = series[t][~series[t].index.duplicated(keep="last")]

    if len(series) < 2:
        return {"n": len(abiertas), "matrix": [], "tickers": [],
                "eff_n_weights": None, "eff_n_corr": None,
                "excluded": excluidas, "obs": 0, "days": days}

    # El índice entra como una columna MÁS, no en un cálculo aparte: así comparte
    # exactamente el mismo calendario común que las posiciones. Correlacionarlo
    # por su cuenta lo dejaría sobre otro conjunto de sesiones, y dos números
    # medidos sobre días distintos no se pueden poner en la misma tabla.
    bench_col = None
    bs = _close_series(benchmark)
    if bs is not None and len(bs):
        bcu = _instrument_ccy(benchmark)
        if bcu:
            base, f = _ccy_base_factor(bcu)
            if base == BASE_CCY:
                bser = bs * f
            else:
                bfx = _fx_series_eur(bcu)
                bser = (bs * bfx.reindex(bs.index.union(bfx.index)).sort_index()
                        .ffill().reindex(bs.index)) if bfx is not None else None
            if bser is not None:
                bench_col = f"__{benchmark}"
                s = bser.dropna()
                s.index = s.index.normalize()
                series[bench_col] = s[~s.index.duplicated(keep="last")]

    df = pd.DataFrame(series).dropna()          # calendario COMÚN, no rellenado
    df = df.tail(days + 1)
    rets = np.log(df / df.shift(1)).dropna()
    if len(rets) < CORR_MIN_OBS:
        return {"n": len(abiertas), "matrix": [], "tickers": list(df.columns),
                "eff_n_weights": None, "eff_n_corr": None,
                "excluded": excluidas, "obs": len(rets), "days": days,
                "why": f"sólo {len(rets)} sesiones en común; hacen falta {CORR_MIN_OBS}"}

    corr = rets.corr()
    # Contra el índice, posición a posición: dice CUÁL de ellas ata la cartera
    # al mercado. La beta lo dice del conjunto y no señala a ninguna.
    vs_bench = None
    if bench_col is not None and bench_col in corr.columns:
        vs_bench = {c: round(float(corr.loc[c, bench_col]), 3)
                    for c in corr.columns if c != bench_col}
        corr = corr.drop(index=bench_col, columns=bench_col)
    tk = list(corr.columns)
    peso = {p["ticker"]: p["market_value"] for p in abiertas}
    # Los pesos se RENORMALIZAN sobre lo que entra en la matriz. Usar el peso
    # sobre la cartera entera repartiría entre las incluidas un capital que no
    # está aquí, y el N efectivo saldría más alto de lo que es.
    total = sum(peso[t] for t in tk)   # `tk` ya no incluye el índice
    w = np.array([peso[t] / total for t in tk])
    R = corr.to_numpy(float)
    pesos = [float(x) for x in w]
    eff_w = _effective_n(pesos)
    eff_c = _effective_n(pesos, R.tolist())

    # El par que más se parece: es lo accionable de toda esta sección.
    peor = None
    for i in range(len(tk)):
        for j in range(i + 1, len(tk)):
            r = float(R[i, j])
            if peor is None or r > peor["rho"]:
                peor = {"a": tk[i], "b": tk[j], "rho": round(r, 3)}

    nombre = {p["ticker"]: (p.get("name") or p["ticker"]) for p in abiertas}
    return {"n": len(abiertas), "tickers": tk,
            "names": [nombre.get(t, t) for t in tk],
            "weights": [round(float(x) * 100, 2) for x in w],
            "matrix": [[round(float(R[i, j]), 3) for j in range(len(tk))]
                       for i in range(len(tk))],
            "eff_n_weights": (round(eff_w, 2) if eff_w is not None else None),
            "eff_n_corr": (round(eff_c, 2) if eff_c is not None else None),
            "most_correlated": peor,
            "vs_benchmark": vs_bench, "benchmark_ticker": benchmark,
            "obs": len(rets), "days": days, "excluded": excluidas}


@app.route("/api/cartera/correlacion")
def api_cartera_correlacion():
    try:
        d = int(request.args.get("days", CORR_DAYS))
    except (TypeError, ValueError):
        d = CORR_DAYS
    d = max(CORR_MIN_OBS, min(2520, d))
    bench = request.args.get("benchmark", "SPY")
    try:
        return _json_response(_cartera_correlacion(d, bench), max_age=0)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/cartera/rendimiento")
def api_cartera_rendimiento():
    bench = request.args.get("benchmark", "SPY")
    try:
        return _json_response(_cartera_returns(
            bench, rango=request.args.get("range"),
            desde=request.args.get("from"), hasta=request.args.get("to")), max_age=0)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/cartera/zonas")
def api_cartera_zonas():
    """La zona del índice para cada posición ABIERTA de la cartera.

    Es la unión que faltaba: esta aplicación sabía decir que un activo está en
    Capitulación y sabía que ese activo estaba en la cartera, y no lo cruzaba.
    Eran dos webs compartiendo menú.

    Los símbolos salen del libro, no de la petición: así esta ruta no sirve para
    barrer tickers ajenos, y lo que devuelve ya lo sabía quien la llama.

    Con la caché fría cada instrumento cuesta una descarga de 25 años, así que
    la petición tiene PRESUPUESTO: lo que no da tiempo a calcular se devuelve
    como `pending` y el cliente vuelve a llamar. Preferimos una tabla que se
    completa por partes a una petición que tarda medio minuto y el navegador
    corta a la mitad.
    """
    with _cartera_conn() as c:
        rows = c.execute("SELECT ticker, side, quantity FROM movements").fetchall()
    net: dict[str, float] = {}
    for tk, side, q in rows:
        if not tk or side == "div":            # un dividendo no da ni quita títulos
            continue
        net[tk] = net.get(tk, 0.0) + (q or 0.0) * (1 if side == "buy" else -1)
    open_tk = sorted(t for t, q in net.items() if q > 1e-9)

    out, pending = {}, []
    deadline = time.time() + ZONES_BUDGET_S
    for t in open_tk:
        cached = _zone_cache.get(t.upper().strip())
        warm = bool(cached and time.time() - cached[0] < CACHE_TTL)
        if not warm and time.time() > deadline:
            pending.append(t)
            continue
        try:
            out[t] = _zone_of(t)
        except NoHistory:
            # Cotiza pero no tiene gráfico: es un hecho asentado del instrumento,
            # no un fallo pasajero, y decirlo con su nombre evita que alguien
            # busque el error en su cartera.
            out[t] = {"error": "sin histórico que puntuar"}
        except BadSymbol:
            out[t] = {"error": "símbolo no válido"}
        except Exception:
            # Un tropiezo de Yahoo NO se cachea como respuesta: vuelve a la cola.
            pending.append(t)
    return _json_response({"zones": out, "pending": pending,
                           "vol_w": VOL_W_DEFAULT, "tf": "daily"}, max_age=0)


@app.route("/api/instrumento")
def api_instrumento():
    """Divisa y último precio de un símbolo, para que el formulario pueda decir
    EN QUÉ MONEDA hay que teclear el precio.

    El panel siempre asumió la divisa nativa del instrumento sin decirlo en
    ninguna parte. Quien copiaba el importe en euros que le cobró su bróker por
    una acción estadounidense se metía un error del tamaño del EURUSD, y nada
    en la pantalla chirriaba: el número era plausible.
    """
    sym = (request.args.get("symbol") or "").strip().upper()
    if not sym:
        return jsonify({"error": "falta el símbolo"}), 400
    try:
        safe_symbol(sym)
    except BadSymbol as e:
        return jsonify({"error": str(e)}), 400
    try:
        price, ccy = _quote_meta(sym)
    except Exception:
        price, ccy = None, ""
    base, factor = _ccy_base_factor(ccy) if ccy else ("", 1.0)
    return _json_response({"symbol": sym, "ccy": ccy or "", "last": price,
                           # GBp cotiza en PENIQUES. Un instrumento de Londres a
                           # "850" no vale 850 libras, y el formulario tiene que
                           # poder avisarlo antes de que alguien teclee el precio
                           # cien veces más grande de lo que es.
                           "base_ccy": base, "factor": factor}, max_age=0)


# ── Proyecto de Abraham (analisis) — servido, no integrado ──────────────────
# Es OTRO proyecto: su pipeline, sus datos y sus páginas HTML, que él genera. Se
# sirven TAL CUAL, sin releer ni reinterpretar una sola línea suya. Van bajo
# /abraham y no en su propio puerto para que entren por el mismo túnel SSH que
# el resto — su unidad `dashboard-serve` pediría un `-L` más, y además apunta al
# 8765, que en esta máquina ya lo ocupa escalera.
ABRAHAM_DIR = os.environ.get(
    "ABRAHAM_DASHBOARD_DIR", "/home/alex/bots/analisis-abraham/dashboard")


@app.route("/abraham")
def abraham_root():
    # Sin la barra final, los enlaces relativos de sus páginas (vendor/…) se
    # resolverían contra la raíz del sitio en vez de contra su carpeta.
    return redirect("/abraham/", code=302)


@app.route("/abraham/")
@app.route("/abraham/<path:filename>")
def abraham_page(filename: str = "index.html"):
    """Sirve el dashboard construido de Abraham. Si falta una de sus TRES páginas
    generadas lo dice, en vez de un 404 pelado: la diferencia entre «no existe»
    y «falta pasar el pipeline» es justo lo que hace falta saber para arreglarlo.
    Un fichero cualquiera que no esté sigue siendo un 404 normal — decir «sin
    construir» de un `vendor/` ausente sería mandar a nadie a ninguna parte."""
    built = {"index.html", "panorama.html", "oportunidades.html"}
    if filename in built and not os.path.isfile(os.path.join(ABRAHAM_DIR, filename)):
        return Response(
            "<h1>Proyecto Abraham — sin construir</h1><p>No encuentro "
            f"<code>{os.path.join(ABRAHAM_DIR, filename)}</code>. Es una página "
            "que genera su pipeline: <code>src.fetch</code> → "
            "<code>src.export</code> → <code>src.build_dashboard</code> / "
            "<code>src.build_panorama</code> / <code>src.build_opportunities</code>.</p>",
            status=503, mimetype="text/html")
    return send_from_directory(ABRAHAM_DIR, filename)


# ── Página de swing de zonas-v2, servida por este puerto ────────────────────
# zonas-v2 corre en su propio servicio (:8775) y este panel en el :8771, que es
# el único que cruza el túnel SSH. En vez de pedir un `-L` más, se reenvía la
# página: es HTML autocontenido —sin scripts, sin peticiones externas, CSS y SVG
# en línea—, así que reenviar el cuerpo es TODO el trabajo. Nada que reescribir.
ZONAS_V2_URL = os.environ.get("ZONAS_V2_URL", "http://127.0.0.1:8775")
ZONAS_V2_TIMEOUT = 30


@app.route("/swing")
def swing():
    """Reenvía /swing de zonas-v2. Si su servicio no responde lo dice, en vez de
    devolver una página en blanco que se leería como «no hay datos»."""
    try:
        req = urllib.request.Request(f"{ZONAS_V2_URL}/swing")
        with urllib.request.urlopen(req, timeout=ZONAS_V2_TIMEOUT) as r:
            body = r.read()
    except Exception as exc:
        return Response(
            "<h1>Swing intradía — servicio no disponible</h1>"
            f"<p>No he podido hablar con zonas-v2 en <code>{ZONAS_V2_URL}</code>: "
            f"{html.escape(str(exc))}.</p><p>Comprueba el servicio con "
            "<code>systemctl --user status zonas-v2-dashboard</code>.</p>",
            status=503, mimetype="text/html")
    return Response(body, mimetype="text/html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "")
    if len(q.strip()) < 2:
        return jsonify({"results": []})
    return jsonify({"results": _yahoo_search(q)})


@app.route("/api/cartera/<int:mid>", methods=["DELETE"])
def api_cartera_delete(mid):
    with _cartera_conn() as c:
        c.execute("DELETE FROM movements WHERE id=?", (mid,))
    return jsonify(_cartera_payload())


@app.route("/api/cartera/<int:mid>", methods=["PATCH"])
def api_cartera_edit(mid):
    """Corregir un movimiento en su sitio.

    Antes sólo se podía borrar y volver a teclear, y eso convertía el error más
    común de todos —un dedazo en el precio— en un borrado sobre el único estado
    de esta aplicación que no se puede reconstruir desde ningún sitio. Un fallo
    a mitad de camino dejaba el libro con un movimiento MENOS.

    Sólo se tocan los campos que vienen; lo que no viaja en el cuerpo se queda
    como estaba. Un campo presente pero ilegible es un error, no un cero: quien
    escribe `precio: "abc"` no está pidiendo que su compra pase a valer nada.
    """
    d = request.get_json(force=True, silent=True) or {}
    with _cartera_conn() as c:
        row = c.execute("SELECT id,date,ticker,name,kind,side,quantity,price,fee,note "
                        "FROM movements WHERE id=?", (mid,)).fetchone()
        if row is None:
            return jsonify({"error": "ese movimiento ya no existe"}), 404
        cols = ("id", "date", "ticker", "name", "kind", "side", "quantity", "price", "fee", "note")
        cur = dict(zip(cols, row, strict=True))

        upd = {}
        campo_es = {"quantity": "cantidad", "price": "precio", "fee": "comisión"}
        qty_raw = None
        for f in ("quantity", "price", "fee"):
            if f in d:
                v = _num(d[f])
                if v is None:
                    return jsonify({"error": f"«{campo_es[f]}» no es un número"}), 400
                if f == "quantity":
                    # El SIGNO se guarda aparte antes de perderlo: es lo que
                    # desempata el lado cuando la palabra que llega no está en
                    # el vocabulario, y la base guarda siempre el valor absoluto.
                    qty_raw = v
                upd[f] = abs(v) if f != "fee" else v
        if "date" in d:
            upd["date"] = _norm_date(d["date"]) if d["date"] else ""
        if "note" in d:
            upd["note"] = str(d["note"]).strip()
        if "side" in d:
            # La cantidad que manda es la NUEVA si viene en la misma edición: el
            # signo de la vieja no dice nada del movimiento que se está guardando.
            upd["side"] = _norm_side(d["side"], qty_raw if qty_raw is not None else cur["quantity"])
        if "ticker" in d and str(d["ticker"]).strip():
            tk = str(d["ticker"]).strip().upper()
            name, kind = str(d.get("name", "")).strip(), str(d.get("kind", "")).strip()
            if _looks_like_isin(tk) and not d.get("symbol"):
                sym, rn, kd = _resolve_symbol(tk)
                if sym:
                    tk, kind, name = sym, (kind or kd), (name or rn)
            if tk != cur["ticker"]:
                # Cambiar de instrumento y quedarse el nombre viejo deja una fila
                # que dice una cosa y vale otra. Si no llega nombre nuevo, se
                # borra el anterior antes que heredarlo.
                upd["ticker"], upd["name"] = tk, name
            elif name:
                upd["name"] = name
            upd["kind"] = _instrument_kind(tk, kind or cur["kind"])

        if not upd:
            return jsonify(_cartera_payload())
        sets = ", ".join(f"{k}=?" for k in upd)
        c.execute(f"UPDATE movements SET {sets} WHERE id=?", (*upd.values(), mid))
    if "ticker" in upd:
        _seed_geo_async(_geo_unknown([upd["ticker"]]))
    return jsonify(_cartera_payload())


@app.route("/api/cartera/ter", methods=["POST"])
def api_cartera_ter():
    """Declarar el TER de un instrumento, en porcentaje anual.

    Se guarda en su propia tabla y sobrevive a que se borren todos los
    movimientos de ese activo: el folleto de un fondo no cambia porque tú lo
    vendas, y volver a teclearlo al recomprarlo sería trabajo repetido para un
    dato que no ha variado.
    """
    d = request.get_json(force=True, silent=True) or {}
    tk = str(d.get("ticker", "")).strip().upper()
    if not tk:
        return jsonify({"error": "falta el instrumento"}), 400
    raw = d.get("ter")
    if raw in (None, ""):
        ter = None                                  # borrar es declarar que no se sabe
    else:
        ter = _num(raw)
        if ter is None or ter < 0 or ter > 10:
            # Un TER por encima del 10% anual no existe; casi siempre es un
            # 0,12 tecleado como 12. Rechazarlo evita que el coste proyectado
            # a veinte años salga por las nubes y nadie entienda por qué.
            return jsonify({"error": "el TER va en % anual (p. ej. 0,12). "
                                     "Fuera del rango 0–10 no es un TER."}), 400
    with _cartera_conn() as c:
        if ter is None:
            c.execute("UPDATE instrument_meta SET ter=NULL, ter_source=NULL, "
                      "ter_date=NULL WHERE ticker=?", (tk,))
            c.execute("DELETE FROM instrument_meta WHERE ter IS NULL AND target IS NULL")
        else:
            fuente = str(d.get("source", "")).strip() or "declarado a mano"
            hoy = time.strftime("%Y-%m-%d")
            c.execute("INSERT INTO instrument_meta(ticker,ter,ter_source,ter_date) "
                      "VALUES(?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
                      "ter=excluded.ter, ter_source=excluded.ter_source, "
                      "ter_date=excluded.ter_date", (tk, ter, fuente, hoy))
    return jsonify(_cartera_payload())


@app.route("/api/cartera/objetivo", methods=["POST"])
def api_cartera_objetivo():
    """Peso objetivo de un instrumento, en % de la cartera.

    Vive en la misma tabla que el TER porque es lo mismo: una decisión sobre el
    INSTRUMENTO que no se puede deducir de los movimientos. Los objetivos no
    tienen que sumar 100 exacto — el reparto los normaliza — porque exigirlo
    sería pedir una aritmética que nadie hace a mano.
    """
    d = request.get_json(force=True, silent=True) or {}
    tk = str(d.get("ticker", "")).strip().upper()
    if not tk:
        return jsonify({"error": "falta el instrumento"}), 400
    raw = d.get("target")
    if raw in (None, ""):
        tgt = None
    else:
        tgt = _num(raw)
        if tgt is None or tgt < 0 or tgt > 100:
            return jsonify({"error": "el peso objetivo va en % de la cartera (0–100)"}), 400
    with _cartera_conn() as c:
        c.execute("INSERT INTO instrument_meta(ticker,target) VALUES(?,?) "
                  "ON CONFLICT(ticker) DO UPDATE SET target=excluded.target", (tk, tgt))
        # Una fila que ya no dice nada se borra: si no, la tabla acumula
        # instrumentos fantasma que nadie volverá a mirar.
        c.execute("DELETE FROM instrument_meta WHERE ter IS NULL AND target IS NULL")
    return jsonify(_cartera_payload())


def _cartera_aportaciones():
    """Calendario de aportaciones: qué entró, qué salió y qué se cobró, por mes.

    Sale de la MISMA reconstrucción que la rentabilidad, así que no puede
    discrepar de ella. Y mide lo que se despliega en TÍTULOS, no lo que entra
    en la cuenta del bróker: este programa no ve transferencias, sólo compras.
    Un mes con dinero parado en efectivo aparece aquí como un mes sin aportar,
    y eso hay que decirlo en vez de dejar que se lea como otra cosa.
    """
    r = _reconstruct_portfolio("SPY")
    if r.get("empty"):
        return {"rows": [], "stats": _contrib_stats([]), "empty": True,
                "base": BASE_CCY}
    fechas = [str(d)[:10] for d in r["idx"]]
    filas = _monthly_flows(fechas, [float(x) for x in r["flows"]],
                           [float(x) for x in r["divs"]])
    hoy = str(pd.Timestamp.today().normalize())[:7]
    return {"rows": filas, "stats": _contrib_stats(filas, today=hoy),
            "empty": False, "excluded": r["excluded"], "base": BASE_CCY}


@app.route("/api/cartera/aportaciones")
def api_cartera_aportaciones():
    try:
        return _json_response(_cartera_aportaciones(), max_age=0)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/cartera/plan", methods=["POST"])
def api_cartera_plan():
    """Declarar el objetivo propio: capital, horizonte y aportación prevista.

    Los tres son opcionales y borrables. Un campo vacío significa «no lo he
    decidido», que no es lo mismo que cero: por eso se guarda NULL y no 0, y
    por eso el progreso correspondiente desaparece en vez de salir a cero.
    """
    d = request.get_json(force=True, silent=True) or {}
    campos, limites = {}, {"capital": (0, 1e12), "horizon_years": (0, 100),
                           "monthly": (0, 1e9), "pending_losses": (0, 1e9)}
    es = {"capital": "capital objetivo", "horizon_years": "horizonte",
          "monthly": "aportación mensual", "pending_losses": "minusvalías pendientes"}
    for k, (lo, hi) in limites.items():
        if k not in d:
            continue
        raw = d[k]
        if raw in (None, ""):
            campos[k] = None
            continue
        v = _num(raw)
        if v is None or not (lo <= v <= hi):
            return jsonify({"error": f"«{es[k]}» fuera de rango"}), 400
        campos[k] = v
    if not campos:
        return jsonify({"error": "no llegó ningún campo"}), 400
    with _cartera_conn() as c:
        c.execute("INSERT INTO portfolio_goal(id) VALUES(1) "
                  "ON CONFLICT(id) DO NOTHING")
        sets = ", ".join(f"{k}=?" for k in campos)
        c.execute(f"UPDATE portfolio_goal SET {sets} WHERE id=1", tuple(campos.values()))
        # Un plan sin ningún campo declarado se borra: así `_portfolio_goal`
        # devuelve None y la pantalla vuelve a ofrecer declararlo, en vez de
        # enseñar una ficha vacía que parece rota.
        c.execute("DELETE FROM portfolio_goal WHERE capital IS NULL "
                  "AND horizon_years IS NULL AND monthly IS NULL "
                  "AND pending_losses IS NULL")
    return jsonify(_cartera_estado())


def _cartera_estado():
    """El bloque de cabecera: qué tengo, cómo voy, y qué merece una mirada.

    Junta en una sola llamada lo que si no serían cinco, porque es lo que se
    lee en los primeros diez segundos y no puede aparecer a trozos.

    LA COBERTURA ES LA PIEZA QUE HACE HONESTO A TODO LO DEMÁS. Rentabilidad,
    caída y correlación se calculan sobre las posiciones que tienen serie de
    precios utilizable; si eso es el 70% del patrimonio, esas cifras describen
    ese 70%. Publicarlas sin decirlo las convierte en una afirmación sobre la
    cartera entera que nadie ha comprobado.
    """
    p = _cartera_payload()
    s = p["summary"]
    abiertas = [x for x in p["positions"] if x["qty"] > 1e-9]
    valoradas = [x for x in abiertas if x["valued"]]
    total = sum(x["market_value"] for x in valoradas) or 0.0

    # ¿De qué posiciones hay serie utilizable? Es el hecho que gobierna la
    # entrada al gráfico, a la rentabilidad y a la correlación.
    con_hist = {}
    for x in abiertas:
        try:
            serie = _close_series(x["ticker"])
            con_hist[x["ticker"]] = bool(serie is not None and len(serie))
        except Exception:
            con_hist[x["ticker"]] = False

    def cuota(pred):
        v = sum(x["market_value"] for x in valoradas if pred(x))
        return round(v / total * 100, 1) if total > 1e-9 else None

    # La zona se lee de la caché y NO se fuerza: calcularla aquí obligaría a
    # descargar 25 años por instrumento antes de pintar la cabecera. Con la
    # caché fría el dato es `None` —«todavía no lo sé»— y nunca 0%, que diría
    # «ninguna tiene zona» y es una afirmación distinta y falsa.
    zonas_vistas = sum(1 for x in valoradas
                       if (_zone_cache.get(x["ticker"].upper().strip()) or (0, {}))[1].get("zone"))
    cobertura = {
        "analisis": cuota(lambda x: con_hist.get(x["ticker"])),
        "ter": cuota(lambda x: x.get("ter") is not None),
        "zona": (cuota(lambda x: (_zone_cache.get(x["ticker"].upper().strip()) or (0, {}))[1].get("zone"))
                 if zonas_vistas else None),
        "valorado": (round(len(valoradas) / len(abiertas) * 100, 1) if abiertas else None),
        "sin_valorar": len(abiertas) - len(valoradas),
    }

    rend = ytd = corr = None
    try:
        rend = _cartera_returns("SPY")
    except Exception:
        pass
    try:
        ytd = _cartera_returns("SPY", rango="ytd")
    except Exception:
        pass
    try:
        corr = _cartera_correlacion(payload=p)
    except Exception:
        pass

    aport = None
    try:
        aport = _cartera_aportaciones()
    except Exception:
        pass

    # Capital aportado NETO: lo desplegado menos lo retirado. `invested` no
    # sirve para esto — es el coste de lo que sigue abierto, y una posición
    # cerrada con beneficio desaparece de ahí como si nunca se hubiera aportado.
    flujos = (rend or {}).get("flows") or {}
    aportado = (round(flujos.get("aportado", 0.0) - flujos.get("retirado", 0.0), 2)
                if flujos else None)

    goal = _portfolio_goal()
    doce = None
    if aport and aport.get("rows"):
        doce = round(sum(r["in"] for r in aport["rows"][-12:]), 2)
    plan = _goal_progress(goal, total, doce)

    try:
        splits = _cartera_splits(payload=p).get("pending") or []
    except Exception:
        splits = []
    hechos = {
        "splits": splits,
        "total": total,
        "positions": [{**x, "has_history": con_hist.get(x["ticker"], False)}
                      for x in abiertas],
        "n_undated": s.get("n_undated", 0),
        "months_since_contribution": (aport or {}).get("stats", {}).get("months_since"),
        "eff_n_corr": (corr or {}).get("eff_n_corr"),
        "eff_n_weights": (corr or {}).get("eff_n_weights"),
        "coverage_pct": cobertura["analisis"],
    }
    avisos = _attention(hechos)

    t = (rend or {}).get("twr") or {}
    bt = (rend or {}).get("benchmark_twr") or {}
    vs = None
    if (rend or {}).get("twr_price_only") and bt.get("total") is not None:
        vs = round(rend["twr_price_only"]["total"] - bt["total"], 6)

    return {
        "value": round(total, 2),
        "contributed": aportado,
        "result": s.get("total_return"),
        "twr": t.get("total"), "twr_annualized": t.get("annualized"),
        "tir": (rend or {}).get("tir"),
        "annualizable": (rend or {}).get("annualizable", False),
        "ytd": ((ytd or {}).get("twr") or {}).get("total"),
        "ytd_from": (ytd or {}).get("from"),
        "benchmark_ticker": (rend or {}).get("benchmark_ticker", "SPY"),
        "vs_benchmark": vs,
        "drawdown": (rend or {}).get("drawdown"),
        "volatility": (rend or {}).get("volatility"),
        "coverage": cobertura,
        "attention": avisos,
        "n_attention": len([a for a in avisos if a["level"] == "warn"]),
        "goal": plan,
        "n_positions": len(abiertas),
        "base": BASE_CCY,
    }


@app.route("/api/cartera/estado")
def api_cartera_estado():
    try:
        return _json_response(_cartera_estado(), max_age=0)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


def _realized_this_year(movs, year):
    """Plusvalía FIFO realizada en el año en curso, en euros.

    Se obtiene corriendo la MISMA contabilidad dos veces —con todo el libro y
    con el libro hasta el 31 de diciembre anterior— y restando. Reimplementar
    aquí un FIFO «por año» sería una segunda copia de la regla más delicada del
    proyecto, y dos copias divergen: bastaría un arreglo en una para que la
    pantalla de impuestos dejara de cuadrar con la de resultados.

    Importa porque decide el TRAMO: una ganancia nueva no tributa al tipo más
    bajo si ese año ya se han realizado otras.
    """
    corte = f"{year}-01-01"
    previos = [m for m in movs if (m.get("date") or "") and m["date"] < corte]
    mkt = _Market()
    total = sum(p["realized_fifo"] for p in _compute_positions(movs, mkt)
                if p["realized_fifo"] is not None)
    antes = sum(p["realized_fifo"] for p in _compute_positions(previos, mkt)
                if p["realized_fifo"] is not None) if previos else 0.0
    return round(total - antes, 2)


@app.route("/api/cartera/simular-venta")
def api_cartera_simular_venta():
    """Qué dejaría una venta: lo exacto del libro y lo estimado de la ley.

    Los dos bloques salen SEPARADOS a propósito. El coste FIFO, el ingreso y el
    resultado se derivan de movimientos ya apuntados y se pueden comprobar. El
    impuesto es una estimación que depende de una ley que cambia y de cosas que
    este programa no ve: el resto de tus rentas del ahorro, tus minusvalías de
    ejercicios anteriores y si tributas en territorio foral.
    """
    tk = (request.args.get("ticker") or "").strip().upper()
    if not tk:
        return jsonify({"error": "falta el instrumento"}), 400

    p = _cartera_payload()
    pos = next((x for x in p["positions"] if x["ticker"] == tk and x["qty"] > 1e-9), None)
    if pos is None:
        return jsonify({"error": "no hay posición abierta en ese instrumento"}), 404
    if not pos.get("valued"):
        return jsonify({"error": f"esa posición no se puede valorar: {pos.get('why')}"}), 422

    lots = [[l["qty"], l["unit_cost"], l["date"]] for l in (pos.get("lots") or [])]

    def num(name, defecto=None):
        v = _num(request.args.get(name))
        return defecto if v is None else v

    qty = num("qty")
    if qty is None:
        importe = num("amount")
        # Por importe: se traduce a títulos con el ÚLTIMO precio, y se dice.
        # Un importe no es una orden: el precio al que se ejecutaría no lo sabe
        # nadie todavía.
        qty = (importe / pos["last"]) if (importe and pos.get("last")) else pos["qty"]
    # NO se recorta a lo que hay. `simulate_sale` ya calcula sobre los títulos
    # que existen y devuelve cuántos faltaban; recortar aquí borraría ese dato y
    # la pantalla contestaría a una pregunta distinta de la que se hizo, sin
    # decirlo. Pedir 999 cuando hay 9 tiene que verse.
    qty = max(0.0, float(qty))

    year = int(str(pd.Timestamp.today().normalize())[:4])
    ya = num("other_gains")
    auto = ya is None
    if auto:
        try:
            ya = max(0.0, _realized_this_year(p["movements"], year))
        except Exception:
            ya = 0.0
    guardadas = (_portfolio_goal() or {}).get("pending_losses") or 0.0
    pendientes = num("pending_losses", guardadas)

    sim = _simulate_sale(lots, qty, pos["last"], fee=num("fee", 0.0),
                         fx=pos.get("fx") or 1.0,
                         other_gains=ya, pending_losses=pendientes)

    # La regla de los dos meses: sólo se puede mirar hacia ATRÁS, a lo que ya
    # está apuntado. Si va a haber recompra mañana, este programa no lo sabe.
    compras = [m["date"] for m in p["movements"]
               if m["ticker"] == tk and m["side"] == "buy" and m.get("date")]
    hoy = str(pd.Timestamp.today().normalize())[:10]
    # Cotizado o no lo decide `instrument_kind`, que es donde vive la regla.
    # Una copia aquí se desviaba: `0P0001CLDK.F` lleva punto y SIGUE siendo un
    # fondo no cotizado, y su ventana no es de dos meses sino de un año.
    cotiza = _instrument_kind(tk, pos.get("kind") or "") != "Fondo"
    recompras = _repurchase_risk(hoy, compras, listed=cotiza) if sim["result"] < 0 else []

    return _json_response({
        **sim,
        "ticker": tk, "name": pos.get("name") or tk, "ccy": pos.get("ccy"),
        "price": pos.get("last"), "fx": pos.get("fx"),
        "held": pos["qty"], "all_lots": pos.get("lots") or [],
        "other_gains_auto": auto, "year": year, "brackets_year": _TRAMOS_ANO,
        "losses_stored": round(float(guardadas), 2),
        "loss_note": _loss_note(sim["result"]),
        "repurchase": recompras, "listed": cotiza,
        "base": BASE_CCY,
    }, max_age=0)


@app.route("/api/cartera/divisa")
def api_cartera_divisa():
    """Exposición por divisa, en sus dos lecturas.

    La de COTIZACIÓN es exacta y dice poco: un fondo mundial cotizado en euros
    sale aquí al 100% en euros y dentro lleva dos tercios de dólares. La
    ECONÓMICA se deriva de la transparencia por países que ya hace el mapa, y
    es la que responde de qué depende el patrimonio de verdad.

    Se devuelven las dos porque cada una engaña por su lado si va sola.
    """
    p = _cartera_payload()
    cotiza = _ccy_quote(p["positions"], base=BASE_CCY)
    try:
        abiertas = [x for x in p["positions"] if x["qty"] > 1e-9]
        res = geo.country_exposure(abiertas, geo.load_table())
        economica = _ccy_economic(res.get("countries") or [],
                                  mapped_eur=res.get("mapped_eur"),
                                  base=BASE_CCY)
        economica["portfolio_eur"] = p["summary"]["market_value"]
        economica["no_geography_eur"] = res.get("no_geography_eur")
    except Exception as e:
        economica = {"error": str(e)}
    return _json_response({"quote": cotiza, "economic": economica,
                           "base": BASE_CCY}, max_age=0)


def _cartera_splits(payload=None):
    """Splits posteriores a alguna compra y todavía sin resolver.

    Se detecta, se enseña QUÉ cambiaría y se espera. El programa no puede saber
    si la cantidad del libro ya está en la escala nueva —«10 títulos» es el
    mismo número antes y después—, así que decidir por su cuenta sería
    reescribir el libro de alguien sobre una suposición.
    """
    p = payload or _cartera_payload()
    abiertas = [x for x in p["positions"] if x["qty"] > 1e-9]
    with _cartera_conn() as c:
        ack = {}
        for tk, fecha in c.execute("SELECT ticker, split_date FROM split_ack"):
            ack.setdefault(tk, set()).add(fecha)

    filas, sin_mirar = [], []
    for pos in abiertas:
        tk = pos["ticker"]
        sp = _splits_of(tk)
        if sp is None:
            sin_mirar.append(tk)
            continue
        movs = [m for m in p["movements"] if m["ticker"] == tk]
        for s in _splits_pending(movs, sp, acked=ack.get(tk, ())):
            prev = _splits_preview(movs, s)
            filas.append({**s, "ticker": tk, "name": pos.get("name") or tk,
                          "rows": prev, "cost_ok": _cost_ok(prev),
                          "qty_now": pos["qty"],
                          "qty_if_applied": round(pos["qty"] * s["ratio"], 6)})
    return {"pending": filas, "unchecked": sin_mirar,
            "n": len(filas), "base": BASE_CCY}


@app.route("/api/cartera/splits")
def api_cartera_splits():
    try:
        return _json_response(_cartera_splits(), max_age=0)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/cartera/splits", methods=["POST"])
def api_cartera_splits_resolver():
    """Aplicar un split al libro, o marcarlo como ya tenido en cuenta.

    Aplicar REESCRIBE movimientos, que es la operación más delicada de todo el
    panel. Por eso, antes de tocar nada: se saca una copia de seguridad, se
    recalcula la previsualización desde la base (no se acepta la que mandó el
    cliente, que pudo quedarse vieja) y se comprueba que el COSTE TOTAL no se
    mueve. Si esa comprobación falla, no se escribe una sola fila.
    """
    d = request.get_json(force=True, silent=True) or {}
    tk = str(d.get("ticker", "")).strip().upper()
    fecha = str(d.get("date", ""))[:10]
    accion = str(d.get("action", "")).strip()
    if not tk or not fecha or accion not in ("apply", "ack"):
        return jsonify({"error": "faltan instrumento, fecha o acción"}), 400

    if accion == "ack":
        with _cartera_conn() as c:
            c.execute("INSERT INTO split_ack(ticker,split_date,action) VALUES(?,?,?) "
                      "ON CONFLICT(ticker,split_date) DO UPDATE SET action=excluded.action",
                      (tk, fecha, "ack"))
        return jsonify(_cartera_payload())

    sp = _splits_of(tk) or []
    split = next((s for s in sp if str(s["date"])[:10] == fecha), None)
    if split is None:
        return jsonify({"error": "ese split no consta en la fuente de datos"}), 404

    with _cartera_conn() as c:
        cur = c.execute("SELECT id,date,ticker,side,quantity,price FROM movements "
                        "WHERE ticker=?", (tk,))
        cols = [x[0] for x in cur.description]
        movs = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    filas = _splits_preview(movs, split)
    if not filas:
        return jsonify({"error": "no hay movimientos anteriores a ese split"}), 400
    if not _cost_ok(filas):
        # Cinturón: el ajuste sólo es seguro porque no mueve el coste. Si aquí
        # no cuadra, escribir sería dejar el libro peor de lo que estaba.
        return jsonify({"error": "el ajuste no conserva el coste total; "
                                 "no se ha tocado nada"}), 409

    # Copia ANTES de reescribir. Es la única operación del panel que modifica
    # movimientos ya apuntados, y el libro es el único estado que no se puede
    # reconstruir desde ninguna parte. Si la copia falla, se dice y se sigue:
    # negarse a aplicar por eso dejaría el libro mal para siempre, que es peor.
    copia = None
    try:
        import backup_cartera as _bk
        # Devuelve un CÓDIGO de salida, no una ruta: 0 copia nueva, 2 idéntica a
        # la anterior (que también deja el libro a salvo), 1 fallo. Se traduce
        # aquí porque un entero suelto en la respuesta no le dice nada a nadie.
        rc = _bk.make_backup(CARTERA_DB, _bk.DEFAULT_DIR, quiet=True)
        copia = {0: "copia nueva guardada",
                 2: "ya había una copia idéntica"}.get(rc, "la copia falló")
    except Exception as e:
        copia = f"sin copia previa ({e})"

    with _cartera_conn() as c:
        c.executemany("UPDATE movements SET quantity=?, price=? WHERE id=?",
                      [(f["qty_after"], f["price_after"], f["id"]) for f in filas])
        c.execute("INSERT INTO split_ack(ticker,split_date,action) VALUES(?,?,?) "
                  "ON CONFLICT(ticker,split_date) DO UPDATE SET action=excluded.action",
                  (tk, fecha, "applied"))
    out = _cartera_payload()
    out["split_applied"] = {"ticker": tk, "date": fecha, "ratio": split["ratio"],
                            "n": len(filas), "backup": copia}
    return jsonify(out)


@app.route("/api/cartera/clases")
def api_cartera_clases():
    """Reparto por clase de activo, con la MISMA tabla que usa el mapa.

    Reusar esa clasificación y no inventar otra es la mitad del valor: dos
    taxonomías en la misma aplicación acaban discrepando, y a partir de ahí hay
    que decidir cuál vale cada vez que se mira.
    """
    p = _cartera_payload()
    try:
        tabla = (geo.load_table() or {}).get("instruments") or {}
    except Exception:
        tabla = {}
    return _json_response(_by_asset_class(p["positions"], tabla, base=BASE_CCY),
                          max_age=0)


@app.route("/api/cartera/diario")
def api_cartera_diario():
    """Cronología de hechos PROPIOS: ni noticias, ni mercado, ni pronósticos.

    Dos fuentes y ninguna más: los movimientos, que apuntó una persona, y los
    hitos de la serie de rendimiento —máximos, la peor caída y su recuperación—,
    que se derivan de precios. Que un mes no tenga nada que contar es una
    respuesta válida y no un fallo del diario.
    """
    p = _cartera_payload()
    nav = fechas = caida = None
    try:
        r = _reconstruct_portfolio("SPY")
        if not r.get("empty"):
            con_div = [float(f - d) for f, d in zip(r["flows"], r["divs"], strict=True)]
            nav = _nav_series([float(v) for v in r["port"]], con_div)
            fechas = [str(d)[:10] for d in r["idx"]]
            caida = _drawdown(nav, list(r["idx"]))
    except Exception:
        nav = fechas = caida = None
    return _json_response({"events": _diary(p["movements"], nav=nav, dates=fechas,
                                            drawdown=caida),
                           "base": BASE_CCY}, max_age=0)


@app.route("/api/cartera/rebalanceo")
def api_cartera_rebalanceo():
    """Qué comprar con una aportación para acercarse a los pesos objetivo.

    SIN VENDER NADA, y no por comodidad: en España cada venta con plusvalía es
    un hecho imponible, así que rebalancear vendiendo lo que sobra paga
    impuestos hoy para cuadrar unos decimales de peso. Comprar lo que falta con
    dinero nuevo llega al mismo sitio sin pasar por Hacienda.
    """
    try:
        cash = float(request.args.get("cash", 0) or 0)
    except (TypeError, ValueError):
        cash = 0.0
    p = _cartera_payload()
    abiertas = [x for x in p["positions"] if x["qty"] > 1e-9 and x.get("market_value")]
    actual = {x["ticker"]: x["market_value"] for x in abiertas}
    objetivos = {x["ticker"]: x["target"] for x in abiertas if x.get("target")}
    total = sum(actual.values())
    filas = []
    if objetivos and total > 0:
        suma_obj = sum(objetivos.values())
        for x in abiertas:
            tgt = objetivos.get(x["ticker"])
            if tgt is None:
                continue
            obj_norm = tgt / suma_obj * 100
            actual_pct = x["market_value"] / total * 100
            filas.append({"ticker": x["ticker"], "name": x.get("name") or x["ticker"],
                          "kind": x.get("kind") or "",
                          "now_pct": round(actual_pct, 2), "target_pct": round(obj_norm, 2),
                          "drift_pp": round(actual_pct - obj_norm, 2),
                          "drift_eur": round(x["market_value"] - obj_norm / 100 * total, 2)})
        filas.sort(key=lambda r: r["drift_pp"])
    compras = _rebalance(actual, objetivos, cash) if cash > 0 else {}
    sin_objetivo = [x["ticker"] for x in abiertas if not x.get("target")]
    return _json_response({
        "rows": filas, "buys": compras, "cash": cash,
        "total": round(total, 2),
        "targets_sum": round(sum(objetivos.values()), 2) if objetivos else 0.0,
        # Las posiciones sin objetivo NO se reparten y NO se cuentan como cero:
        # que algo no tenga peso asignado significa que nadie ha decidido, no
        # que deba desaparecer de la cartera.
        "untargeted": sin_objetivo, "base": BASE_CCY}, max_age=0)


@app.route("/api/cartera/clear", methods=["POST"])
def api_cartera_clear():
    with _cartera_conn() as c:
        c.execute("DELETE FROM movements")
    return jsonify(_cartera_payload())


# Serie y splits viven en la MISMA entrada, no en dos cachés paralelas.
# Tenerlas separadas era un fallo silencioso: `_close_series` sólo guarda los
# splits cuando FALLA su caché, así que en cuanto la de splits se desalojaba
# —tope de 64 entradas— y la de series seguía viva, `_splits_of` llamaba,
# encontraba la serie cacheada, no repoblaba nada y devolvía `None` hasta que
# expirase el TTL. El panel decía «sin consultar» para instrumentos que sí podía
# mirar, y un split podía pasar desapercibido: justo lo que la función existe
# para impedir. Compartiendo entrada, no pueden desincronizarse.
_series_cache: dict[str, tuple[float, object, list]] = {}


def _splits_of(ticker: str):
    """Splits declarados por la fuente, o None si aún no se sabe.

    `None` y `[]` son respuestas distintas: la primera es «no lo he mirado» y la
    segunda «no ha habido ninguno». Devolver `[]` en los dos casos haría que un
    instrumento sin consultar pareciera limpio.
    """
    t = ticker.upper().strip()
    _close_series(t)                     # rellena la entrada, sin pedir de más
    hit = _series_cache.get(t)
    return hit[2] if hit else None


def _close_series(ticker: str):
    """Daily close series (date-indexed), cached. None if the ticker is unavailable.

    A permanent "no bars" answer (`NoHistory`) is cached like any result; a
    transient failure is NOT, so a momentary Yahoo hiccup cannot pin a position
    out of the chart for the whole TTL.
    """
    t = ticker.upper().strip()
    hit = _series_cache.get(t)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    splits = []
    try:
        df = fetch_daily(t, years=25)
        # Los splits llegan en la MISMA respuesta, y se guardan con la serie:
        # este es el único sitio por el que pasa el frame entero.
        splits = df.attrs.get("splits") or []
        di = pd.DatetimeIndex(pd.to_datetime(df["date"]))
        if di.tz is not None:                    # Yahoo returns tz-aware -> make naive
            di = di.tz_localize(None)
        s = pd.Series(df["close"].to_numpy(float), index=di).sort_index()
        s = s[~s.index.duplicated(keep="last")]
    except NoHistory:
        s = None                                 # settled fact -> cache it
    except Exception:
        return None                              # transient -> retry next call
    _cache_put(_series_cache, t, (time.time(), s, splits), cap=CACHE_MAX)
    return s


def _proxy_series(original: str, proxy: str):
    """(series, deviation) if `proxy` may stand in for `original`, else (None, why).

    Every condition is a way the substitution could be wrong, checked at RUN
    TIME rather than trusted from the table: a listing gets delisted, a share
    class is re-denominated, a symbol is recycled. A proxy that stops agreeing
    must stop being used — silently charting the wrong instrument is worse than
    charting nothing, which is the failure this whole feature exists to fix.
    """
    s = _close_series(proxy)
    if s is None or not len(s):
        return None, "el proxy tampoco tiene histórico"
    p_orig, c_orig = _quote_meta(original)
    p_prox, c_prox = _quote_meta(proxy)
    if not c_orig or not c_prox:
        return None, "sin divisa para comparar"
    if c_orig != c_prox:
        return None, f"divisa distinta ({c_orig} vs {c_prox})"
    if not p_orig or not p_prox:
        return None, "sin cotización para comparar"
    dev = abs(p_prox / p_orig - 1.0)
    if dev > PROXY_MAX_DEV:
        return None, f"desvío de precio {dev:.1%} (máx {PROXY_MAX_DEV:.0%})"
    return s, dev


def _ffill_on(series, idx):
    """Reindex a price series onto idx, forward-filling to the last known close."""
    if series is None or not len(series):
        return None
    return series.reindex(series.index.union(idx)).sort_index().ffill().reindex(idx).to_numpy(float)


# Los rangos del selector. `ytd` no es un número de días: es "desde el 1 de
# enero", y resolverlo como 365 días daría otra cosa cada día del año.
RANGOS = {"1m": 30, "3m": 91, "6m": 182, "1y": 365, "3y": 1095, "5y": 1825}


def _resolver_ventana(idx, rango=None, desde=None, hasta=None):
    """(i0, i1) sobre `idx`, o (0, len-1) para todo.

    Las fechas se resuelven contra el ÚLTIMO día de la serie y no contra el
    reloj: si Yahoo aún no ha publicado la barra de hoy, un "1 mes" medido
    desde el reloj empezaría un día antes que el que se ve en la gráfica, y el
    primer punto no coincidiría con el borde del rango.
    """
    n = len(idx)
    if n == 0:
        return 0, 0
    fin = idx[-1]
    ini = None
    if desde:
        try:
            ini = pd.to_datetime(desde)
        except Exception:
            ini = None
    if ini is None and rango:
        r = str(rango).lower()
        if r == "ytd":
            ini = pd.Timestamp(year=fin.year, month=1, day=1)
        elif r in RANGOS:
            ini = fin - pd.Timedelta(days=RANGOS[r])
    if hasta:
        try:
            fin = min(fin, pd.to_datetime(hasta))
        except Exception:
            pass
    i1 = int(np.clip(idx.searchsorted(fin, side="right") - 1, 0, n - 1))
    if ini is None:
        return 0, i1
    # El primer punto es el último cierre ESTRICTAMENTE ANTERIOR al inicio del
    # tramo. Para medir lo que hizo enero hace falta el cierre del 31 de
    # diciembre: sin él, el primer día de enero no tiene contra qué medirse y su
    # rendimiento se pierde. `side="left"` y no `"right"` porque cuando la fecha
    # de inicio CAE en día hábil —el 1 de enero lo es en media Europa— `"right"`
    # se planta encima de ella en vez de en el cierre anterior, y el primer día
    # del tramo volvía a quedarse sin referencia.
    #
    # De paso, "1 año" abarca de verdad 365 días o más, y no 364, que era justo
    # el umbral por debajo del cual el panel se niega a anualizar — y dejaba el
    # preset de un año entero sin cifra anual.
    i0 = int(np.clip(idx.searchsorted(ini, side="left") - 1, 0, i1))
    return i0, i1


def _rebasar(r, i0, i1):
    """Recorta la reconstrucción a [i0, i1] y REBASA las dos líneas de referencia.

    Recortar sin más sería cosmético y engañoso. La línea del índice se
    construye desde el PRIMER movimiento de la cartera: enseñar tres meses de
    cartera contra una posición del índice sembrada hace dos años haría que la
    diferencia visible fuese casi toda historia vieja arrastrada.

    Lo que hace una ventana honesta:

      * el índice se RESIEMBRA el primer día del tramo, comprando con el valor
        que la cartera tenía ese día, y a partir de ahí recibe los mismos
        flujos que la cartera dentro del tramo;
      * "invertido" pasa a ser el capital que había al abrir la ventana más lo
        aportado dentro, de forma que arranca pegado a la cartera y el hueco
        entre las dos líneas es exactamente lo ganado EN ESE TRAMO.

    Las tres líneas salen del mismo punto, que es lo que espera cualquiera que
    elige un rango.
    """
    idx = r["idx"][i0:i1 + 1]
    port = r["port"][i0:i1 + 1]
    flujo = r["flows"][i0:i1 + 1]
    if i0 == 0:
        return idx, port, r["invested"][i0:i1 + 1], (
            None if r["bench_val"] is None else r["bench_val"][i0:i1 + 1])

    # El flujo del día i0 ya está dentro de port[i0], así que no vuelve a sumar.
    aport = np.concatenate([[0.0], np.cumsum(flujo[1:])]) if len(flujo) > 1 else np.zeros(1)
    invested = port[0] + aport

    bench = None
    bp = r.get("bprice_eur")
    if bp is not None:
        bp = bp[i0:i1 + 1]
        if np.isfinite(bp[0]) and bp[0] > 0:
            part = np.zeros(len(bp))
            part[0] = port[0] / bp[0]
            for k in range(1, len(bp)):
                extra = (flujo[k] / bp[k]) if (np.isfinite(bp[k]) and bp[k] > 0) else 0.0
                part[k] = part[k - 1] + extra
            bench = part * bp
    return idx, port, invested, bench


def _cartera_history(benchmark: str = "SPY", max_points: int = 800,
                     rango=None, desde=None, hasta=None):
    """El gráfico: la misma reconstrucción, submuestreada a `max_points`.

    El submuestreo es SÓLO para pintar. Cualquier cuenta —rentabilidad, riesgo—
    tiene que salir de `_reconstruct_portfolio`, a resolución diaria: un TWR
    calculado sobre uno de cada cuatro días coloca los flujos en el día que no
    es y encadena tramos que nunca existieron.
    """
    r = _reconstruct_portfolio(benchmark)
    if r.get("empty"):
        return r["payload"]
    i0, i1 = _resolver_ventana(r["idx"], rango, desde, hasta)
    if i1 - i0 < 1:                      # ventana sin dos puntos: no hay línea
        i0, i1 = 0, len(r["idx"]) - 1
        rango = "all"
    idx, port, invested, bench_val = _rebasar(r, i0, i1)
    step = max(1, len(idx) // max_points)
    sl = slice(None, None, step)

    def clean(a):
        return [None if not np.isfinite(x) else round(float(x), 2) for x in a[sl]]

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in idx[sl]],
        "portfolio": clean(port), "invested": clean(invested),
        "benchmark": (clean(bench_val) if bench_val is not None else None),
        "benchmark_ticker": benchmark, "base": BASE_CCY,
        "range": (rango or "all"),
        # Un tramo recortado lleva el índice RESEMBRADO en su primer día, así
        # que el cliente tiene que poder decir que lo que compara empieza ahí y
        # no en la primera compra de la cartera.
        "rebased": i0 > 0,
        "first": str(r["idx"][0])[:10], "last": str(r["idx"][-1])[:10],
        # Which holdings this chart does NOT represent, so the UI can say so
        # instead of drawing a shortfall that is really a missing data feed.
        "excluded": r["excluded"], "covered": not r["excluded"],
        # Which holdings are charted through a sibling listing, and why any
        # configured proxy was refused — a borrowed series must never be
        # indistinguishable from the instrument's own.
        "proxied": r["proxied"], "proxy_refused": r["refused"],
    }


def _reconstruct_portfolio(benchmark: str = "SPY"):
    """Reconstruct the portfolio market value over time, plus a SAME-CASHFLOW
    benchmark: every buy/sell deploys/withdraws the same cash into the index.

    INVARIANT: `portfolio` and `invested` must always describe the SAME set of
    positions. A ticker Yahoo will not chart is dropped from BOTH — valuation
    and cashflow — and named in `excluded`. Dropping it from only one side is
    what made this chart disagree with the positions table by the full cost of
    the excluded holdings.
    """
    with _cartera_conn() as c:
        cur = c.execute("SELECT date,ticker,side,quantity,price,fee FROM movements ORDER BY date")
        cols = [d[0] for d in cur.description]
        movs = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    movs = [m for m in movs if m["date"]]
    # Los dividendos salen del carril de la compraventa ANTES de cualquier
    # cuenta. Este bucle sólo sabe de dos cosas —títulos que entran y dinero que
    # se despliega— y un dividendo no es ninguna de las dos. Cayendo por el
    # `else` de más abajo se habría tratado como una VENTA: le habría restado
    # títulos a la posición y habría sacado del benchmark un dinero que nunca se
    # retiró, así que la línea de la cartera se separaba de la tabla de
    # posiciones un poco más con cada cobro.
    #
    # Pero NO se tiran: van a su propia serie, porque la rentabilidad sí los
    # necesita. Un reparto hace caer el precio sin que se haya perdido nada, y
    # sin contarlo el TWR restaría rentabilidad en cada cobro.
    divs = [m for m in movs if m["side"] == "div"]
    movs = [m for m in movs if m["side"] != "div"]
    empty = {"dates": [], "portfolio": [], "invested": [], "benchmark": None,
             "benchmark_ticker": benchmark, "base": BASE_CCY, "excluded": [],
             "covered": True, "proxied": []}
    if not movs:
        return {"empty": True, "payload": empty}
    end = pd.Timestamp.today().normalize()
    all_tickers = sorted({m["ticker"] for m in movs})
    # Price series and quote metadata for the whole book at once: this call used
    # to cost the SUM of every instrument's round trip (~1.5 s for five).
    proxies = [HISTORY_PROXY[t] for t in all_tickers if t in HISTORY_PROXY]
    _prefetch(_close_series, all_tickers + proxies + [benchmark])
    _prefetch(_quote_meta, all_tickers + proxies + [benchmark])
    series = {t: _close_series(t) for t in all_tickers}

    # A listing with no history of its own may borrow a sibling's, if the
    # sibling still passes every agreement check. Live valuation is untouched:
    # only the SHAPE OF THE PAST comes from the proxy.
    proxied, refused = [], {}
    for t in all_tickers:
        if (series.get(t) is not None and len(series[t])) or t not in HISTORY_PROXY:
            continue
        via = HISTORY_PROXY[t]
        s, info = _proxy_series(t, via)
        if s is None:
            refused[t] = f"{via}: {info}"
        else:
            series[t] = s
            proxied.append({"ticker": t, "via": via, "dev": round(float(info), 5)})

    # Keep only what can be valued across the whole window, and drop the rest
    # from the cashflow too so the two lines stay comparable. "Valued" means a
    # price series AND a known currency: an unknown currency cannot be put on a
    # EUR axis, and assuming 1.0 would plot dollars as euros.
    ccy_of = {t: _instrument_ccy(t) for t in all_tickers}
    tickers = [t for t in all_tickers
               if series.get(t) is not None and len(series[t]) and ccy_of[t]]
    excluded = [t for t in all_tickers if t not in tickers]
    movs = [m for m in movs if m["ticker"] in set(tickers)]
    if not movs:
        return {"empty": True,
                "payload": dict(empty, excluded=excluded, covered=False, proxied=proxied)}

    # AFTER the filter: if the oldest movement belonged to an excluded ticker,
    # starting there would open the chart with a stretch of flat zero.
    start = pd.to_datetime(min(m["date"] for m in movs))
    tccy = {t: ccy_of[t] for t in tickers}
    bench = _close_series(benchmark)
    bccy = _instrument_ccy(benchmark)

    idx = None
    if bench is not None and len(bench):
        bi = bench.index[(bench.index >= start) & (bench.index <= end)]
        idx = bi if len(bi) >= 2 else None
    if idx is None:
        for s in series.values():
            if s is None:
                continue
            di = s.index[(s.index >= start) & (s.index <= end)]
            idx = di if idx is None else idx.union(di)
    if idx is None or len(idx) < 2:
        return {"empty": True, "payload": empty}
    idx = idx.sort_values()
    dts = pd.to_datetime([m["date"] for m in movs])
    poss = np.clip(idx.searchsorted(dts), 0, len(idx) - 1)

    # EUR-per-unit fx array aligned to idx, per currency. `None` means "not
    # knowable" and the caller must drop the ticker — the old code substituted
    # an array of 1.0 here, which is how a USD holding got charted as if euros.
    fxcache = {}

    def fx_arr(cu):
        if cu in fxcache:
            return fxcache[cu]
        base, _f = _ccy_base_factor(cu)
        s = _fx_series_eur(cu)
        if base == BASE_CCY:
            arr = np.ones(len(idx))               # genuinely EUR: 1.0 is the rate
        else:
            a = _ffill_on(s, idx) if s is not None else None
            arr = None if a is None else pd.Series(a).ffill().bfill().to_numpy(float)
            if arr is None or not np.isfinite(arr).all():
                fxcache[cu] = None
                return None
        fxcache[cu] = arr
        return arr

    # Second pass of the same invariant: a ticker whose fx series is unavailable
    # for this window leaves BOTH lines, and says so. Do it before any maths so
    # `port` and `invested` are still built from an identical set of movements.
    no_fx = [t for t in tickers if fx_arr(tccy[t]) is None]
    if no_fx:
        tickers = [t for t in tickers if t not in set(no_fx)]
        excluded = sorted(set(excluded) | set(no_fx))
        movs = [m for m in movs if m["ticker"] in set(tickers)]
        if not movs:
            return {"empty": True,
                    "payload": dict(empty, excluded=excluded, covered=False, proxied=proxied)}
        dts = pd.to_datetime([m["date"] for m in movs])
        poss = np.clip(idx.searchsorted(dts), 0, len(idx) - 1)

    port = np.zeros(len(idx))
    for t in tickers:
        # Every entry here has a series, a currency, an fx array and a matching
        # cashflow. No `continue` may drop a position at this point — that is
        # precisely how valuation and cost fell out of sync.
        price = _ffill_on(series[t], idx)
        price_eur = price * fx_arr(tccy[t])                # native -> EUR (historical fx)
        qc = np.zeros(len(idx))
        for k, m in enumerate(movs):
            if m["ticker"] == t:
                qc[poss[k]] += (m["quantity"] if m["side"] == "buy" else -m["quantity"])
        qty = np.cumsum(qc)
        port += np.where(np.isfinite(price_eur), qty * price_eur, 0.0)

    bfx = fx_arr(bccy) if bccy else None
    bprice = _ffill_on(bench, idx)
    bprice_eur = (bprice * bfx) if (bprice is not None and bfx is not None) else None
    inv_c = np.zeros(len(idx)); bsh_c = np.zeros(len(idx))
    for k, m in enumerate(movs):
        q, px, fee = m["quantity"], m["price"], (m["fee"] or 0.0)
        rate = fx_arr(tccy[m["ticker"]])[poss[k]]          # EUR at the movement date
        cash = ((q * px + fee) if m["side"] == "buy" else -(q * px - fee)) * rate
        inv_c[poss[k]] += cash
        if bprice_eur is not None and np.isfinite(bprice_eur[poss[k]]) and bprice_eur[poss[k]] > 0:
            bsh_c[poss[k]] += cash / bprice_eur[poss[k]]
    invested = np.cumsum(inv_c)
    bench_val = (np.cumsum(bsh_c) * bprice_eur) if bprice_eur is not None else None

    # Los dividendos, a su propia serie y al cambio de SU día. Sólo los de
    # instrumentos que siguen dentro: si una posición quedó fuera del gráfico
    # por falta de serie o de tipo de cambio, su reparto tiene que salir con
    # ella o la rentabilidad tendría un ingreso sin el activo que lo generó.
    dentro = set(tickers)
    div_c = np.zeros(len(idx))
    for m in divs:
        if m["ticker"] not in dentro:
            continue
        pos = int(np.clip(idx.searchsorted(pd.to_datetime(m["date"])), 0, len(idx) - 1))
        rate = fx_arr(tccy[m["ticker"]])[pos]
        div_c[pos] += (m["quantity"] * m["price"] - (m["fee"] or 0.0)) * rate

    return {"empty": False, "idx": idx, "port": port, "invested": invested,
            "bench_val": bench_val, "bprice_eur": bprice_eur,
            "flows": inv_c, "divs": div_c,
            "excluded": excluded, "proxied": proxied, "refused": refused,
            "tickers": tickers}


@app.route("/api/cartera/history")
def api_cartera_history():
    bench = request.args.get("benchmark", "SPY")
    try:
        return _json_response(_cartera_history(
            bench, rango=request.args.get("range"),
            desde=request.args.get("from"), hasta=request.args.get("to")), max_age=0)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


SEEDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "analysis", "seed_country_weights.py")
SEED_TIMEOUT = 180
_seed_lock = threading.Lock()
_seeding: set[str] = set()


def _geo_unknown(tickers) -> list[str]:
    """Which of these the country table has never seen."""
    known = geo.load_table().get("instruments") or {}
    return sorted({t for t in tickers if t and t not in known})


def _seed_geo_async(tickers) -> None:
    """Fetch country weights for newly bought instruments, off the request path.

    Buying something the table does not know would otherwise leave it stranded
    in `unmapped` until a human remembered to run the seeder. This closes that
    gap without ever putting a scrape between the user and a response: the HTTP
    reply is already on its way, and the map picks the weights up on its next
    refresh.

    A SUBPROCESS rather than an import, for two reasons. It is the same entry
    point the timer uses, so there is only one code path to trust; and a scraper
    that hangs or dies cannot take a thread of the dashboard down with it — the
    timeout is enforced from outside.
    """
    with _seed_lock:
        todo = [t for t in tickers if t not in _seeding]
        _seeding.update(todo)
    if not todo:
        return                       # already in flight: importing the same file
                                     # twice must not fan out into two scrapes

    def run():
        try:
            for t in todo:
                try:
                    subprocess.run(
                        [sys.executable, SEEDER, "--only", t, "--quiet"],
                        cwd=os.path.dirname(SEEDER), timeout=SEED_TIMEOUT,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        check=False)
                except (OSError, subprocess.SubprocessError):
                    pass             # best effort: the page still says the
                                     # instrument has no country table yet
        finally:
            with _seed_lock:
                _seeding.difference_update(todo)

    threading.Thread(target=run, name="seed-geo", daemon=True).start()


@app.route("/api/cartera/geo")
def api_cartera_geo():
    """Look-through country exposure, optionally narrowed to one asset class.

    An unknown `clase` is rejected rather than quietly widened to everything:
    a typo returning the full portfolio would read as "this class holds all of
    it", which is the most misleading answer available.
    """
    clase = request.args.get("clase", "all")
    if clase not in ("all", *geo.ASSET_CLASSES):
        return jsonify({"error": f"clase desconocida: {clase}"}), 400
    payload = _cartera_payload()
    open_pos = [p for p in payload["positions"] if p["qty"] > 1e-9]
    res = geo.country_exposure(open_pos, geo.load_table(),
                               asset_class=None if clase == "all" else clase)
    # The portfolio total the map is a view of, so the page can show what share
    # of the whole book the map is actually describing.
    res["portfolio_eur"] = payload["summary"]["market_value"]
    # Instruments whose weights are being fetched right now. Told rather than
    # inferred: the page can say "looking this up" and come back for it, instead
    # of guessing from a gap whether something is missing or merely late.
    with _seed_lock:
        res["seeding"] = sorted(_seeding)
    return _json_response(res, max_age=0)


def _prewarm() -> None:
    """Warm both caches after startup so the first click is instant.

    The REGIME list is warmed first and takes priority: /screener and /comité
    fan out over all of it at once, so a cold regime cache costs a visitor the
    whole list serially. `regime.dashboard._prewarm` never actually ran — it
    only fires under `__main__`, and that module is imported, never executed.
    """
    # The portfolio first: it is one page, a handful of symbols, and the only
    # view whose latency the user pays on every single visit.
    try:
        with _cartera_conn() as c:
            held = [r[0] for r in c.execute("SELECT DISTINCT ticker FROM movements")]
        _prefetch(_close_series, [*held, "SPY"])
        _prefetch(_quote_meta, [*held, "SPY"])
    except Exception:
        pass
    # Warm the ENCODED caches — the regime list first, since /screener and
    # /comité fan out over all of it at once. Both regime variants are built
    # back to back so the intermediate payload is reused before it is evicted.
    for sym, _ in REGIME_CURATED:
        try:
            _get_regime(sym, light=True)
            _get_regime(sym, light=False)
        except Exception:
            pass
        time.sleep(0.4)          # gentle with Yahoo, but not 1.2 s x 45 symbols
    for sym, _ in CURATED:
        try:
            _get(sym)
        except Exception:
            pass
        time.sleep(0.4)


def _serve() -> None:
    """Serve with waitress when it is available, else the Werkzeug dev server.

    The dev server spawns one thread per connection with no ceiling and no
    request timeout, so a page holding connections open is enough to exhaust
    it. Waitress gives a bounded worker pool, a connection limit and timeouts.

    It is vendored into ./vendor rather than installed into the interpreter's
    site-packages, because that venv is shared by ~60 running services
    including live trading bots — a dependency resolution there is not worth
    the risk for a read-only dashboard. The fallback keeps the service running
    if the vendor directory is ever missing.
    """
    try:
        from waitress import serve
    except ImportError:
        app.run(host="127.0.0.1", port=PORT, threaded=True)
        return
    serve(app, host="127.0.0.1", port=PORT,
          threads=8,                  # bounded pool instead of one per connection
          connection_limit=64,        # refuse rather than pile up
          channel_timeout=60,         # drop a client that stops talking
          ident="market-zones")       # no server version in the banner


def main() -> None:
    # Launched from a terminal, silence is indistinguishable from a crash: the
    # server blocks on accept() and prints nothing for as long as it works. Say
    # where the panel is, and how to stop it, before that happens.
    print(f"market-zones  ->  http://127.0.0.1:{PORT}", flush=True)
    print("Primera carga lenta: descarga las cotizaciones. Para parar: Ctrl+C",
          flush=True)
    threading.Thread(target=_prewarm, daemon=True).start()
    _serve()


if __name__ == "__main__":
    main()
