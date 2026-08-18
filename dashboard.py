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
from flask import (Flask, Response, jsonify, redirect, render_template, request,
                   send_from_directory)

import geo
# El dominio de la cartera vive en su propio paquete: qué significa un
# movimiento no depende de que haya un navegador delante. Aquí dentro siguen
# siendo detalles internos, así que se reexportan con el guion bajo con el que
# los llama el resto de este fichero.
from cartera.parsing import CARTERA_EXPORT_COLS, COLSYN, FAR_FUTURE
from cartera.parsing import clean_company_name as _clean_company_name
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
from cartera.positions import BASE_CCY
from cartera.positions import compute as _compute_positions
from zones import (WEEKLY, BadSymbol, NoHistory, analyze, fetch_daily,
                   safe_symbol, to_weekly)
from zones.engine import VOL_W_DEFAULT
from zones.target import compute as _compute_target
# The regime panel reuses its own builder + cache (import is side-effect-free;
# its prewarm/run only fire under __main__, which we never trigger here).
from regime.dashboard import CURATED as REGIME_CURATED
from regime.dashboard import _get as regime_get

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
        zip(ts, close, score, zone, stretch, rsi, dd, td, vol, climax)
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
    return c


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
               + urllib.parse.quote(q) + "&quotesCount=%d&newsCount=0&lang=es-ES&region=ES" % limit)
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
        movs = [dict(zip(cols, r)) for r in cur.fetchall()]
    # Normalise on READ as well as on write: rows stored before the classifier
    # existed keep whatever Yahoo said that day, and `_positions` takes the type
    # from the most recent movement — so a single mislabelled entry re-badges the
    # whole position. Derived from the symbol, the two lists cannot disagree.
    for m in movs:
        m["kind"] = _instrument_kind(m.get("ticker"), m.get("kind"))
    positions = _positions(movs)
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
            k = _mov_key(dict(zip(("date", "ticker", "side", "quantity", "price"), r)))
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
    except Exception as exc:              # noqa: BLE001 — el detalle es el diagnóstico
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
        cur = dict(zip(cols, row))

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


@app.route("/api/cartera/clear", methods=["POST"])
def api_cartera_clear():
    with _cartera_conn() as c:
        c.execute("DELETE FROM movements")
    return jsonify(_cartera_payload())


_series_cache: dict[str, tuple[float, object]] = {}


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
    try:
        df = fetch_daily(t, years=25)
        di = pd.DatetimeIndex(pd.to_datetime(df["date"]))
        if di.tz is not None:                    # Yahoo returns tz-aware -> make naive
            di = di.tz_localize(None)
        s = pd.Series(df["close"].to_numpy(float), index=di).sort_index()
        s = s[~s.index.duplicated(keep="last")]
    except NoHistory:
        s = None                                 # settled fact -> cache it
    except Exception:
        return None                              # transient -> retry next call
    _cache_put(_series_cache, t, (time.time(), s), cap=CACHE_MAX)
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


def _cartera_history(benchmark: str = "SPY", max_points: int = 800):
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
        movs = [dict(zip(cols, r)) for r in cur.fetchall()]
    movs = [m for m in movs if m["date"]]
    # Los dividendos salen ANTES de cualquier cuenta. Esta reconstrucción sólo
    # sabe de dos cosas —títulos que entran y dinero que se despliega— y un
    # dividendo no es ninguna de las dos. Cayendo por el `else` de más abajo se
    # habría tratado como una VENTA: le habría restado títulos a la posición y
    # habría sacado del benchmark un dinero que nunca se retiró, así que la
    # línea de la cartera se separaba de la tabla de posiciones un poco más con
    # cada cobro.
    movs = [m for m in movs if m["side"] != "div"]
    empty = {"dates": [], "portfolio": [], "invested": [], "benchmark": None,
             "benchmark_ticker": benchmark, "base": BASE_CCY, "excluded": [],
             "covered": True, "proxied": []}
    if not movs:
        return empty
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
        return dict(empty, excluded=excluded, covered=False, proxied=proxied)

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
        return empty
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
            return dict(empty, excluded=excluded, covered=False, proxied=proxied)
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

    step = max(1, len(idx) // max_points)
    sl = slice(None, None, step)

    def clean(a):
        return [None if not np.isfinite(x) else round(float(x), 2) for x in a[sl]]

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in idx[sl]],
        "portfolio": clean(port), "invested": clean(invested),
        "benchmark": (clean(bench_val) if bench_val is not None else None),
        "benchmark_ticker": benchmark, "base": BASE_CCY,
        # Which holdings this chart does NOT represent, so the UI can say so
        # instead of drawing a shortfall that is really a missing data feed.
        "excluded": excluded, "covered": not excluded,
        # Which holdings are charted through a sibling listing, and why any
        # configured proxy was refused — a borrowed series must never be
        # indistinguishable from the instrument's own.
        "proxied": proxied, "proxy_refused": refused,
    }


@app.route("/api/cartera/history")
def api_cartera_history():
    bench = request.args.get("benchmark", "SPY")
    try:
        return _json_response(_cartera_history(bench), max_age=0)
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
    if clase not in ("all",) + geo.ASSET_CLASSES:
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
        _prefetch(_close_series, held + ["SPY"])
        _prefetch(_quote_meta, held + ["SPY"])
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
