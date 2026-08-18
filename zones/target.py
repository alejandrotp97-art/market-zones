"""Invert the market-zone score into a target price, three independent ways.

This is NOT a forecast. Each method answers a conditional question — "at what
price would TODAY's index READ this zone?" — holding the whole history fixed and
moving only the last close. It is the same kind of level as "at what price does
RSI hit 30", never a claim about where the price will go or when. A price can sit
stretched for years without touching it.

  M1  exact   bisect the composite score_raw(P) to the zone boundary on the real
              engine -> the price at which the panel flips zone. score_raw is the
              PRE-EMA reading (the level the smoothed score converges to if the
              price holds); inverting the EMA-smoothed score is meaningless — the
              EMA has inertia and one day never reaches the target.
  M2  lever   hold the other components at today's value and ask which single
              dimension could carry the score to the boundary, and at what price.
              The binding lever (the one that fires first) names the driver; if a
              lever's required value falls outside 0..100 it cannot do it alone.
  M3  analog  the Mayer multiples at which THIS asset actually sat in that zone,
              translated to today's moving average -> an empirical band, no model
              linearity assumed.

Converge -> the level is trustworthy. Diverge -> the regime has stretched and the
mechanical inversion is leaning on an extrapolation the history does not support.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import (DAILY, VOL_W_DEFAULT, W_REDUCED, Windows, _full_weights,
                     analyze, score_components)

BUY_U, SELL_U = 20.0, 80.0            # Capitulación entry / Euforia entry

# Banda de incertidumbre = k · vol_anualizada. El objetivo es la ENTRADA de la zona;
# la banda muestra cuán profundo suele correr el precio hacia dentro (el suelo se
# perfora hacia abajo, el techo se supera hacia arriba). k se calibró UNA vez sobre
# el estudio causal de 25 años (18 activos, 1526 muestras): la vol realizada predice
# la dispersión de la perforación con Spearman +0.78 (suelo) / +0.82 (techo).
# NO recalibrar seguido: un walk-forward out-of-sample mostró que hacerlo semanal
# EMPEORA el suelo (−5.9% MAE, bias-variance) y solo roza el techo (+2.3%, deriva
# ~5%/año). Re-validar ANUAL o al ampliar el universo → ver research/band_calibration/.
K_BUY, K_SELL = 0.64, 1.16
_CONF = ((0.25, "fiable"), (0.45, "media"))    # umbrales de vol anualizada; resto "amplia"
# Tope de profundidad del suelo: con vol anualizada > ~140% el producto k·vol pasa
# de 1 y el nivel se iría a un PRECIO NEGATIVO. Solo actúa en volatilidad extrema,
# ya dentro de la franja "amplia" que avisa que el nivel no es de fiar.
MAX_DEPTH = 0.90

_CCY = {"DE": "€", "F": "€", "SG": "€", "PA": "€", "AS": "€", "MI": "€",
        "MC": "€", "VI": "€", "BR": "€", "LS": "€", "HE": "€", "L": "£"}
_FIELDS = ("score_raw", "stretch", "rsi", "drawdown", "trend_dev", "volatility")


def currency(symbol: str) -> str:
    suffix = symbol.rsplit(".", 1)[-1].upper() if "." in symbol else ""
    return _CCY.get(suffix, "$")


def _model_setup(model: str, vol_w: float):
    """(weights, field->weight-key, lever fields) for the active model."""
    if model == "full":
        W = _full_weights(vol_w)
        f2k = {"stretch": "stretch", "rsi": "rsi", "drawdown": "drawdown",
               "trend_dev": "trend_dev", "volatility": "vol"}
        return W, f2k, ["stretch", "rsi", "drawdown", "trend_dev"]
    W = dict(W_REDUCED)
    return W, {"stretch": "stretch", "rsi": "rsi"}, ["stretch", "rsi"]


def compute(df: pd.DataFrame, symbol: str, vol_w: float = VOL_W_DEFAULT, *,
            frame: pd.DataFrame | None = None, summary=None,
            windows: Windows | None = None,
            curve_pts: int = 22, iters: int = 17) -> dict | None:
    """Return the target-price block for `symbol`, or None if the history is too
    short to classify. `frame`/`summary` let the caller pass the analyze() result
    it already computed so the current reading is never recomputed. `windows`
    selects the engine horizons: `None` -> `DAILY`; pass `WEEKLY` with a weekly-
    resampled `df` so every re-scored counterfactual is on weekly bars too."""
    w = windows or DAILY
    df = df.reset_index(drop=True)
    if frame is None or summary is None:
        frame, summary = analyze(df, vol_weight=vol_w, windows=windows)
    if summary.model == "none":
        return None

    price = float(summary.close)
    W, f2k, levers = _model_setup(summary.model, vol_w)
    li = len(df) - 1
    cache: dict[float, dict] = {}

    def ev(P: float) -> dict:
        k = round(float(P), 4)
        if k in cache:
            return cache[k]
        d = df.copy()
        d.loc[li, "close"] = float(P)
        # Ruta ligera: la inversión sólo lee `_FIELDS`, y calcular zonas y
        # convicción en cada pasada era ~48% del coste de un resultado que se
        # descarta entero. Mismos valores: es el mismo núcleo que `analyze()`.
        f = score_components(d, vol_weight=vol_w, windows=windows)
        r = f.iloc[-1]
        out = {c: float(r[c]) for c in _FIELDS}
        cache[k] = out
        return out

    def solve(field: str, target: float, lo: float, hi: float):
        """Price where ev(P)[field] == target (field increasing in P)."""
        g = lambda P: ev(P)[field]
        glo, ghi = g(lo), g(hi)
        for _ in range(40):
            if glo <= target <= ghi:
                break
            if target < glo:
                hi, ghi = lo, glo
                lo *= 0.6
                glo = g(lo)
            else:
                lo, glo = hi, ghi
                hi *= 1.6
                ghi = g(hi)
            if lo < 1e-9 or hi > price * 1e5:
                return None
        for _ in range(iters):
            mid = (lo + hi) / 2.0
            if g(mid) < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    cur = ev(price)

    # --- M1 : exact composite inversion = the zone-boundary prices --------------
    m1_buy = solve("score_raw", BUY_U, price * 0.4, price)
    m1_sell = solve("score_raw", SELL_U, price, price * 2.0)

    # --- M2 : per-lever, others held at today ----------------------------------
    def m2(U: float, is_buy: bool):
        rows = []
        for field in levers:
            wk = f2k[field]
            others = sum(W[f2k[c]] * cur[c] for c in
                         (f for f in _FIELDS[1:] if f2k.get(f) in W and f != field))
            tgt = (U - others) / W[wk]
            if not (0.0 < tgt < 100.0):
                rows.append((field, None))
                continue
            lo, hi = (price * 0.3, price) if is_buy else (price, price * 2.0)
            rows.append((field, solve(field, tgt, lo, hi)))
        hit = [(c, p) for c, p in rows if p is not None]
        binder = (max(hit, key=lambda x: x[1]) if is_buy
                  else min(hit, key=lambda x: x[1])) if hit else (None, None)
        return {"lever": binder[0], "price": binder[1], "all": rows}

    m2_buy, m2_sell = m2(BUY_U, True), m2(SELL_U, False)

    # --- M3 : historical analog via the Mayer multiple -------------------------
    close = frame["close"].astype(float).to_numpy()
    ma_win = w.stretch_full if summary.model == "full" else w.stretch_reduced
    ma = pd.Series(close).rolling(ma_win, min_periods=ma_win).mean().to_numpy()
    mayer = close / ma
    score_s = frame["score"].astype(float).to_numpy()
    ma_today = float(ma[-1])

    # --- vol anualizada para el ancho de banda ---------------------------------
    # ppy = barras/año del timeframe activo (252 diario / 52 semanal), ya en las
    # ventanas: así la vol queda anualizada y comparable, y la banda no cambia de
    # escala entre diario y semanal.
    ppy = w.zmin_full
    # Los retornos se sacan de la serie COMPLETA y se descartan los no finitos: una
    # barra <=0 o NaN invalida sus dos retornos adyacentes y se van. Filtrar las
    # barras ANTES del diff empalmaría los extremos del hueco y fabricaría un
    # retorno que nunca ocurrió.
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.diff(np.log(close))
    lr = lr[np.isfinite(lr)]
    vw = min(ppy, len(lr))
    band_vol = (float(np.std(lr[-vw:], ddof=1) * np.sqrt(ppy))
                if vw > 5 else float("nan"))
    # Sin vol medible no hay confianza que declarar (None -> el panel no pinta chip).
    conf = (next((lab for thr, lab in _CONF if band_vol < thr), "amplia")
            if np.isfinite(band_vol) else None)

    def analog(is_buy: bool):
        thr, wide = (BUY_U, 25.0) if is_buy else (SELL_U, 75.0)
        mask = (score_s <= thr) if is_buy else (score_s >= thr)
        m = mayer[mask & np.isfinite(mayer)]
        used = thr
        if len(m) < 10:
            mask = (score_s <= wide) if is_buy else (score_s >= wide)
            m = mayer[mask & np.isfinite(mayer)]
            used = wide
        if len(m) < 5 or not np.isfinite(ma_today):
            return None
        lo, mid, hi = (float(np.nanquantile(m, q)) for q in (0.25, 0.50, 0.75))
        return {"lo": lo * ma_today, "mid": mid * ma_today, "hi": hi * ma_today,
                "n": int(len(m)), "thr": used}

    a_buy, a_sell = analog(True), analog(False)

    def side(m1, m2v, m3, u):
        # Consensus = median of M1 (model) and M3 (history) only. M2 does NOT
        # vote: its price freezes the other four components, so it is a diagnostic
        # (which lever drives the flip, or "none can alone"), not a real level —
        # and it shares M1's engine, so letting it vote would double-count the
        # model against the single independent voice (M3).
        pts = [p for p in (m1, (m3 or {}).get("mid")) if p is not None]
        pts.sort()
        cons = (pts[len(pts) // 2] if len(pts) % 2
                else (pts[len(pts) // 2 - 1] + pts[len(pts) // 2]) / 2.0) if pts else None
        # banda: el objetivo (consenso) es la ENTRADA; `band` es el borde típico
        # hacia dentro de la zona (compra: hacia abajo; venta: hacia arriba).
        is_buy = u <= 50
        band = None
        if cons is not None and np.isfinite(band_vol):
            k = K_BUY if is_buy else K_SELL
            band = (cons * (1.0 - min(k * band_vol, MAX_DEPTH)) if is_buy
                    else cons * (1.0 + k * band_vol))
        return {
            "u": u,
            "m1": _r(m1), "m2": {"lever": m2v["lever"], "price": _r(m2v["price"])},
            "m3": None if not m3 else {"lo": _r(m3["lo"]), "mid": _r(m3["mid"]),
                                       "hi": _r(m3["hi"]), "n": m3["n"], "thr": m3["thr"]},
            "consensus": _r(cons),
            "pct": None if cons is None else round((cons - price) / price * 100.0, 1),
            "band": _r(band),
            "band_pct": None if band is None or cons in (None, 0)
                        else round((band - cons) / cons * 100.0, 1),
        }

    buy_side = side(m1_buy, m2_buy, a_buy, BUY_U)
    sell_side = side(m1_sell, m2_sell, a_sell, SELL_U)

    # --- counterfactual curve for the chart ------------------------------------
    # El rango incluye las BANDAS: son contenido del gráfico, y dejarlas fuera las
    # dibujaba recortadas contra el borde. Se calcula después de `side` por eso.
    lo = min(x for x in (m1_buy, price * 0.5, buy_side["band"]) if x is not None) * 0.9
    hi = max(x for x in (m1_sell, price * 1.5, sell_side["band"]) if x is not None) * 1.1
    grid = np.linspace(lo, hi, curve_pts)
    curve = [[round(float(P), 2), round(ev(P)["score_raw"], 2)] for P in grid]

    return {
        "ccy": currency(symbol), "model": summary.model,
        "price": round(price, 2), "score": _r(summary.score),
        "score_raw": _r(summary.score_raw),
        "vol": round(band_vol * 100.0, 1) if np.isfinite(band_vol) else None,
        "conf": conf,
        "buy": buy_side,
        "sell": sell_side,
        "curve": curve,
    }


def _r(x, nd: int = 2):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    # Un activo sub-dólar (token, penny stock) se aplasta a 0.0 con 2 decimales, y
    # un nivel de "0.0" no es un precio: es basura que además rompe cualquier
    # división por él. Bajo 1 se conservan ~4 cifras significativas.
    if 0 < abs(v) < 1:
        nd = 3 - int(np.floor(np.log10(abs(v))))
    return round(v, nd)
