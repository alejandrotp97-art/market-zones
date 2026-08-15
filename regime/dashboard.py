#!/usr/bin/env python3
"""Market Regime dashboard — the validated engine on screen (:8772).

Serves the causal Score, the calibrated regime FSM, the 4 axes, and the
conditional forward scenarios. Same house pattern as the other dashboards
(127.0.0.1 + SSH tunnel), cache + single-flight + gzip.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import sys
import threading
import time
from bisect import insort

import numpy as np
import pandas as pd
from flask import Flask, Response, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zones import fetch_daily
from regime import analyze
# _forward is reused read-only to derive the UNCONDITIONAL baseline (presentation
# metric). The engine / inference (analogs.conditional_stats) is NOT modified.
from regime.analogs import HORIZONS, MIN_BOOT_N, _forward, conditional_stats

app = Flask(__name__)
PORT = 8772
YEARS = 25
CACHE_TTL = 600
PHASE_TAIL = 120        # days of trail on the phase map
MIN_CAL_N = 20          # analogues required before a walk-forward point counts

CURATED = [
    # Índices de renta variable
    ("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("^RUT", "Russell 2000"),
    ("URTH", "MSCI World"), ("EEM", "Emerging Markets"),
    ("^GDAXI", "DAX"), ("^STOXX50E", "EuroStoxx 50"), ("^N225", "Nikkei 225"),
    ("^KS11", "KOSPI"), ("^HSCE", "HSCEI (China H)"),
    # Materias primas / metales
    ("GLD", "Oro"), ("SLV", "Plata"), ("BZ=F", "Brent"),
    # Bonos
    ("TLT", "Bonos 20y+ (TLT)"),
    # Uranio / nuclear
    ("NLR", "VanEck Uranio y Nuclear"), ("URA", "Global X Uranium"),
    ("URNM", "Sprott Uranium Miners"), ("CCJ", "Cameco"),
    # Mineras de oro
    ("GDX", "Mineras oro senior"), ("GDXJ", "Mineras oro junior"),
    # Acciones / otros
    ("UNH", "UnitedHealth"), ("KOS", "Kosmos Energy"), ("HGRAF", "HydroGraph"),
    ("BTC-USD", "Bitcoin"),
]

# A built payload is ~3.4 MB of live Python objects, so this cache is a short
# STAGING buffer, not the serving cache: dashboard.py caches the encoded bytes
# (~75 KB) and only needs the dict long enough to derive both variants.
CACHE_MAX = 4
_cache: dict[str, tuple[float, dict]] = {}
_locks: dict[str, threading.Lock] = {}
_lock = threading.Lock()


def _cache_put(key, value) -> None:
    """Insert and evict oldest-first — callers can type any ticker."""
    _cache[key] = value
    while len(_cache) > CACHE_MAX:
        _cache.pop(next(iter(_cache)))       # dicts keep insertion order


def _get_lock(key) -> threading.Lock:
    """Get-or-create; prunes only idle locks for keys no longer cached."""
    lk = _locks.get(key)
    if lk is None:
        if len(_locks) > 4 * CACHE_MAX:
            for k in [k for k, v in _locks.items()
                      if k not in _cache and not v.locked()]:
                _locks.pop(k, None)
        lk = _locks[key] = threading.Lock()
    return lk


def _c(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _r(x, n):
    v = _c(x)
    return None if v is None else round(v, n)


def _labelled(r) -> bool:
    """Does this row carry a regime label?

    `is not None` is NOT enough: assigning a list of `None`s to a DataFrame
    column turns them into float NaN, and `nan is not None` is True. Unlabelled
    burn-in rows therefore leaked into the calibration sample. The old code
    survived it by accident — `nan == nan` is False, so those queries found zero
    analogues and were dropped one step later — but a dict lookup finds a NaN
    key by identity, so the same rows came back the moment the scan was indexed.
    Test the label properly instead of relying on which comparison happens to
    fail downstream.
    """
    return r is not None and r == r and r != ""


def _expanding_median(v: np.ndarray) -> np.ndarray:
    """Median of the finite values in [0..t], for every t. One O(n log n) pass
    replaces a fresh O(n) `np.median` at each sampled date."""
    out = np.full(len(v), np.nan)
    seen: list[float] = []
    for i, x in enumerate(v):
        if math.isfinite(x):
            insort(seen, float(x))
        m = len(seen)
        if m:
            out[i] = seen[m // 2] if m % 2 else 0.5 * (seen[m // 2 - 1] + seen[m // 2])
    return out


# Effective-N cutoffs for the confidence grade. These apply to n/h — the count
# already divided by the horizon, because overlapping forward windows make `n`
# raw days worth only about n/h independent observations. Cutoffs written for a
# RAW count do not survive that division: the previous pair (30 and 10) demanded
# 7560 and 2520 analogue days inside one regime, and SPY holds 6299 rows in its
# entire 25-year history. "Alta" was unreachable for every asset, always.
CONF_NEFF_ALTA = 5.0
CONF_NEFF_MEDIA = 2.0


def _confidence(scen: dict) -> tuple[str, list[str]]:
    """Grade the conditional scenario: sample weight + is the excess real?

    Sample and interval are read on the SAME horizon. Taking the count at 12m
    while testing the interval at 3m is not conservatism — the two describe
    different estimates, and the pair says nothing about either. The reference
    horizon is 3m when available, otherwise the shortest one that survived.

    "Baja" means the evidence is too thin to judge. "Media" means the sample is
    there but the excess does not clear zero. Collapsing those into one word
    told a user to go find more history when no more history exists.
    """
    excs = [r["excess"] for r in scen.values() if r.get("excess") is not None]
    sign_stable = (len({e > 0 for e in excs}) <= 1) if excs else False
    ref = "3m" if "3m" in scen else next(iter(scen), None)

    neff = 0.0
    ci_w = distinguishable = None
    if ref is not None:
        neff = scen[ref]["n_eff"]
        # Read the row's own `evidence`, never re-derive it. The stored bounds
        # are rounded to four decimals for transport; `evidence` was decided in
        # `_build` on the raw bootstrap output. Recomputing from the rounded
        # pair let a bound that rounds onto the baseline flip the answer — KO's
        # 3m row badges `neg` while the verdict beside it said "indistinguible".
        ev = scen[ref].get("evidence")
        if ev is not None:
            distinguishable = ev in ("pos", "neg")
        if scen[ref]["baseline"] is not None:
            ci_w = scen[ref]["ci_hi"] - scen[ref]["ci_lo"]   # baseline cancels

    if neff >= CONF_NEFF_ALTA and distinguishable and sign_stable:
        conf = "Alta"
    elif neff >= CONF_NEFF_MEDIA:
        conf = "Media"
    else:
        conf = "Baja"

    drivers = [f"N efectivo ({ref}): {neff:.1f}"] if ref is not None else \
        ["Sin horizontes con métricas completas"]
    if ci_w is not None:
        drivers.append(f"IC {'estrecho' if ci_w < 0.08 else 'amplio'} (±{ci_w / 2 * 100:.1f}%)")
    if distinguishable is not None:
        drivers.append("Exceso distinguible de 0" if distinguishable else "Exceso indistinguible de 0")
    return conf, drivers


def _build(symbol: str) -> dict:
    df = fetch_daily(symbol, years=YEARS)
    frame, s = analyze(df)
    fin = frame.dropna(subset=["score"])

    # Vectorized for the same reason as the zones panel: `iterrows()` rebuilds a
    # Series per row, and this loop runs over the whole 25-year history.
    def _col(df, name, nd):
        return [None if not math.isfinite(v) else round(float(v), nd)
                for v in df[name].to_numpy(dtype=float)]
    # Epoch MILLISECONDS regardless of the column's own resolution: fetch_daily
    # yields datetime64[s], so a fixed // 10**6 divides seconds by a million.
    _ts = pd.to_datetime(fin["date"], utc=True).dt.as_unit("ms").astype("int64").to_numpy()
    series = [{"t": int(t), "close": c, "score": s, "vol": v, "regime": g}
              for t, c, s, v, g in zip(_ts, _col(fin, "close", 2), _col(fin, "score", 1),
                                       _col(fin, "vol_p", 1), fin["regime"].tolist())]

    tail = fin.tail(PHASE_TAIL)
    phase = [{"level": lv, "vol": vv}
             for lv, vv in zip(_col(tail, "score", 1), _col(tail, "vol_p", 1))]

    # ── conditional scenarios, framed as EXCESS over the unconditional baseline ──
    sc = conditional_stats(frame, method="regime")
    fret, _fdd, _fvol = _forward(frame["close"])            # read-only reuse
    scen = {}
    skipped = []
    for name, hd in HORIZONS.items():
        h = sc["horizons"].get(name, {})
        if "median" not in h:
            skipped.append(name)
            continue                                        # P0-3: no full metrics -> not shown
        # CONTRACT: every row that lands in `scen` carries a median AND a finite
        # bootstrap CI. Downstream (confidence, opportunity, robustness) does
        # arithmetic on ci_lo/ci_hi, so a row with a NaN interval must never get
        # in — that mismatch used to surface as a 502 on the whole panel.
        if any(_c(h.get(k)) is None for k in ("median", "ci_lo", "ci_hi")):
            skipped.append(name)
            continue
        col = fret[hd]
        base = float(np.nanmedian(col)) if np.isfinite(col).any() else None
        row = {k: _r(h[k], 4) for k in ("median", "p10", "p90", "ci_lo", "ci_hi", "median_dd")}
        row["n"] = h["n"]
        row["n_eff"] = round(h["n"] / hd, 1)                # overlapping windows -> N/h
        row["baseline"] = _r(base, 4)
        row["excess"] = _r((h["median"] - base) if base is not None else None, 4)
        if base is not None:                                # P1-1: evidencia desde el IC del exceso
            elo, ehi = h["ci_lo"] - base, h["ci_hi"] - base
            row["evidence"] = "pos" if elo > 0 else ("neg" if ehi < 0 else "flat")
        else:
            row["evidence"] = None
        scen[name] = row

    # ── honest confidence: effective N + CI width + excess distinguishable
    #    from 0, all read on ONE horizon. NO log(N), NO raw analogue count. ──
    conf, drivers = _confidence(scen)

    # ── P0-4: % of history spent in each regime ──
    dist = fin["regime"].value_counts(normalize=True).mul(100).round(1)

    # ── P1-2: transición desde el régimen actual + dwell medio (descriptivo) ──
    # `if r` keeps NaN (bool(nan) is True), which would insert phantom
    # transitions between unlabelled burn-in rows and the first real regime.
    regs = [r for r in fin["regime"].tolist() if _labelled(r)]
    trans, runs, prev, rl = {}, [], None, 0
    for r in regs:
        if r != prev:
            if prev is not None:
                trans.setdefault(prev, {})
                trans[prev][r] = trans[prev].get(r, 0) + 1
                runs.append((prev, rl))
            prev, rl = r, 1
        else:
            rl += 1
    if prev is not None:
        runs.append((prev, rl))
    nxt = trans.get(s.regime, {})
    tot = sum(nxt.values())
    next_dist = ([{"regime": k, "pct": round(100 * v / tot, 1)}
                  for k, v in sorted(nxt.items(), key=lambda x: -x[1])] if tot else [])
    cur_runs = [L for rg, L in runs if rg == s.regime]
    transition = {"next": next_dist, "n": tot,
                  "dwell_mean": (round(sum(cur_runs) / len(cur_runs)) if cur_runs else None)}

    # ── P1-3: calibración walk-forward STRICTAMENTE causal (horizonte 3m). ──
    #    Una predicción emitida en t solo puede usar análogos cuya ventana
    #    forward COMPLETA cerró en t o antes (j + H <= t); un análogo posterior
    #    mira precios que en t todavía no existían. El baseline se recalcula con
    #    la misma regla: mediana de los forward returns ya realizados en t. Es un
    #    backtest de las propias predicciones (sesgo/cobertura); NO toca la
    #    inferencia que se muestra arriba.
    #    Mismo guard que `validation/core.py` (P5), que es la referencia.
    H = HORIZONS["3m"]
    col = fret[H]
    reg_arr = frame["regime"].to_numpy()
    nrows = len(frame)

    # Precomputed ONCE, then every query is two lookups and a slice. The naive
    # version re-scanned the whole history per sampled date — O(samples x n) of
    # Python-level iteration, 205 ms of the build for a single symbol.
    _fin = np.isfinite(col)
    _n_realized = np.cumsum(_fin)                     # finite forward returns by t
    _exp_median = _expanding_median(col)              # causal baseline at every t
    _by_regime: dict = {}                             # regime -> ascending indices
    for _j in np.flatnonzero(_fin):
        r = reg_arr[_j]
        if _labelled(r):
            _by_regime.setdefault(r, []).append(_j)
    _by_regime = {r: np.asarray(v, dtype=int) for r, v in _by_regime.items()}

    def _causal_analogs(t: int, regime):
        """(baseline_t, excesses of same-regime analogues) usable AT t, or None.

        Admissible j: regime match, forward window closed (j + H <= t), finite.
        """
        if not _labelled(regime):
            return None                               # nothing to condition on
        lim = t - H                                   # last admissible index
        if lim < 1 or _n_realized[lim] < MIN_CAL_N:
            return None
        base_t = _exp_median[lim]                     # causal, as of t
        pos = _by_regime.get(regime)
        if pos is None:
            return None
        k = int(np.searchsorted(pos, lim, side="right"))
        if k < MIN_CAL_N:
            return None
        return (float(base_t), col[pos[:k]] - base_t)

    ok = [i for i in range(nrows) if np.isfinite(col[i]) and _labelled(reg_arr[i])]
    sample = ok[:: max(1, len(ok) // 200)]
    preds, obs, cov = [], [], []
    for t in sample:
        got = _causal_analogs(t, reg_arr[t])
        if got is None:
            continue
        base_t, pe = got
        o = float(col[t] - base_t)                    # realized at t+H: the outcome
        preds.append(float(np.median(pe)))
        obs.append(o)
        cov.append(bool(np.percentile(pe, 2.5) <= o <= np.percentile(pe, 97.5)))
    if preds:
        pa, oa = np.asarray(preds), np.asarray(obs)
        calibration = {"pred_mean": _r(float(pa.mean()), 4), "obs_mean": _r(float(oa.mean()), 4),
                       "mae": _r(float(np.mean(np.abs(oa - pa))), 4),
                       "bias": _r(float(np.mean(oa - pa)), 4),
                       "coverage": round(100 * float(np.mean(cov))), "n": len(preds),
                       "horizon": "3m", "causal": True}
    else:
        calibration = None

    # ── P2-1: Opportunity Score (0-100) — señal/ruido por horizonte, penalizado
    #    por N efectivo bajo. Un exceso enorme con N≈2 NO puntúa alto. ──
    contribs, neffs, widths = [], [], []
    for name in scen:
        r = scen[name]
        if r["baseline"] is None or r["excess"] is None:
            continue
        ch = (r["ci_hi"] - r["ci_lo"]) / 2.0
        neffs.append(r["n_eff"]); widths.append(r["ci_hi"] - r["ci_lo"])
        snr = (r["excess"] / ch) if ch > 1e-9 else 0.0
        contribs.append(math.tanh(0.6 * snr) * (min(1.0, r["n_eff"] / 30.0) ** 0.5))
    opportunity = grade = None
    if contribs:
        opp = max(0.0, min(100.0, 50.0 + 50.0 * (sum(contribs) / len(contribs))))
        opportunity = round(opp)
        grade = ("A+" if opp >= 82 else "A" if opp >= 70 else "B" if opp >= 60
                 else "C" if opp >= 50 else "D")

    # ── P2-2: Robustez de la señal — INDEPENDIENTE de la magnitud del exceso ──
    mean_neff = (sum(neffs) / len(neffs)) if neffs else 0.0
    mean_w = (sum(widths) / len(widths)) if widths else 0.30
    signs = {(scen[n]["excess"] >= 0) for n in scen if scen[n]["excess"] is not None}
    rob = 100.0 * (0.28 * min(1.0, mean_neff / 25.0)          # adecuación muestral
                   + 0.22 * (1.0 - min(1.0, mean_w / 0.15))    # IC estrecho
                   + 0.18 * (1.0 if len(signs) <= 1 else 0.4)  # signo estable entre horizontes
                   + 0.14 * min(1.0, (transition["dwell_mean"] or 0) / 40.0)  # persistencia régimen
                   + 0.18 * (min(1.0, calibration["coverage"] / 95.0) if calibration else 0.5))  # calibración
    rob_level = ("Muy robusta" if rob >= 80 else "Robusta" if rob >= 65 else "Moderada"
                 if rob >= 50 else "Débil" if rob >= 35 else "Muy débil")

    # ── P2-6: evolución de la tesis (as-of, causal, submuestreado ~semanal) ──
    #    Mismo guard j + H <= t que la calibración: cada punto es la tesis que el
    #    panel habría publicado ESE día, no una reconstruida con datos de después.
    thesis = []
    start = max(0, nrows - 520)
    for t in range(start, nrows, 5):
        rt = reg_arr[t]
        if not _labelled(rt):
            continue
        got = _causal_analogs(t, rt)
        if got is None:
            continue
        base_t, pe = got
        lo2, hi2 = np.percentile(pe, 2.5), np.percentile(pe, 97.5)
        thesis.append({"t": int(frame["date"].iloc[t].timestamp() * 1000), "regime": rt,
                       "excess": _r(float(np.median(pe)), 4),
                       "ev": "pos" if lo2 > 0 else ("neg" if hi2 < 0 else "flat"),
                       # P4-5: observed 3m-forward excess at this past date, measured
                       # against the SAME causal baseline (NaN for the last ~63 rows).
                       "obs": (_r(float(col[t] - base_t), 4) if np.isfinite(col[t]) else None)})

    # ── P2-7: auditoría ──
    audit = {"history_len": len(fin), "n_raw_12m": scen.get("12m", {}).get("n"),
             "cal_horizon": (calibration["horizon"] if calibration else None),
             "coverage": (calibration["coverage"] if calibration else None),
             # what was NOT shown and why — a horizon silently missing from the
             # panel is indistinguishable from one that had nothing to say.
             "skipped_horizons": skipped, "min_analogs": MIN_BOOT_N,
             "causal_calibration": bool(calibration)}

    return {
        "symbol": symbol, "as_of": str(s.date.date()), "series": series, "phase": phase,
        "scenarios": scen,
        "regime_dist": {k: float(v) for k, v in dist.items()},
        "transition": transition,
        "calibration": calibration,
        "thesis": thesis,
        "audit": audit,
        "summary": {
            "score": _r(s.score, 1), "regime": s.regime, "dwell": s.dwell,
            "trend_up": bool(s.trend_up),
            "extension": _r(s.extension, 1), "vol": _r(s.vol, 1),
            "cycle": _r(s.cycle, 1), "instability": _r(s.instability, 1),
            "regime_pct": float(dist.get(s.regime, 0.0)),
            "confidence": conf, "conf_drivers": drivers,
            "opportunity": opportunity, "grade": grade,
            "robustness": round(rob), "rob_level": rob_level,
        },
    }


def _get(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    with _lock:
        hit = _cache.get(symbol)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        lk = _get_lock(symbol)
    with lk:
        with _lock:
            hit = _cache.get(symbol)
            if hit and time.time() - hit[0] < CACHE_TTL:
                return hit[1]
        payload = _build(symbol)
        with _lock:
            _cache_put(symbol, (time.time(), payload))
        return payload


@app.route("/")
def index():
    return render_template("index.html", curated=CURATED, default="SPY")


@app.route("/api/regime")
def api_regime():
    symbol = request.args.get("symbol", "SPY")
    try:
        payload = _get(symbol)
    except Exception as e:
        return jsonify({"error": f"No pude cargar '{symbol}': {e}"}), 502
    body = json.dumps(payload, separators=(",", ":")).encode()
    if "gzip" in request.headers.get("Accept-Encoding", ""):
        resp = Response(gzip.compress(body, 6), mimetype="application/json")
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
        return resp
    return Response(body, mimetype="application/json")


def _prewarm():
    for sym, _ in CURATED:
        try:
            _get(sym)
        except Exception:
            pass
        time.sleep(1.2)


if __name__ == "__main__":
    threading.Thread(target=_prewarm, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)
