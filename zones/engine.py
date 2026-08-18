"""Engine: turn an OHLCV frame into the full per-date market-zone breakdown.

Composes the pure pieces (indicators -> weighted score -> EMA(7) smoothing ->
hysteresis zones) and picks the model based on how much history exists:

  >= 200 rows : full model   stretch + rsi + drawdown + trend_dev + volatility,
                the first four in BASE_FULL proportion rescaled by
                (1 - vol_weight); with the default vol_weight=0.10 that is
                0.270*stretch + 0.180*rsi + 0.225*drawdown + 0.225*trend_dev
                + 0.100*volatility
   30-199 rows: reduced model 0.60*stretch(MA20) + 0.40*rsi
   <  30 rows : no zone (null)

CAUSALITY (`causal=True`, the default): every normalization uses only [0..t],
so the score at t is what the index would have printed that day. The in-sample
path (`causal=False`) is kept for A/B comparison only — it z-scores and ranks
against the WHOLE series, which rewrites history as new data arrives.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import classify as C
from . import conviction as CV
from .indicators import (drawdown_score, ema, rsi_wilder, stretch_score,
                         trend_dev_score, volatility_score)

# Base proportions of the four original legs. The volatility leg's weight is
# tunable (`vol_weight`); the four are rescaled by (1 - vol_weight) so the whole
# thing always sums to 1.0 and their relative balance is preserved.
BASE_FULL = {"stretch": 0.30, "rsi": 0.20, "drawdown": 0.25, "trend_dev": 0.25}
VOL_W_DEFAULT = 0.10
W_REDUCED = {"stretch": 0.60, "rsi": 0.40}


def _full_weights(vol_weight: float) -> dict:
    """Full-model weights for a given volatility weight (all sum to 1.0)."""
    s = 1.0 - vol_weight
    w = {k: v * s for k, v in BASE_FULL.items()}
    w["vol"] = vol_weight
    return w
SMOOTH_SPAN = 7
FULL_MIN = 200
SHORT_MIN = 30
# Causal burn-in: how many finite observations of a component must exist before
# it can be normalized against its own past. The full model can afford a year;
# the reduced model runs on 30-199 rows, so it needs a much shorter warm-up or
# it would never produce a score at all.
ZMIN_FULL = 252
ZMIN_REDUCED = 20


@dataclass(frozen=True)
class Windows:
    """Bar-count windows so the SAME engine can score a daily OR a weekly series.

    Every field is a number of BARS, not calendar days: on a weekly series a
    `40` means 40 weeks. `DAILY` reproduces the historical literals exactly, so
    `analyze(df)` (windows=None) stays byte-identical to before this parameter
    existed. `WEEKLY` scales each horizon by ~5 trading days per week, so
    "weekly" means the same CALENDAR span measured on weekly bars.
    """
    smooth_span: int        # EMA smoothing span of the score
    full_min: int           # min bars before the full model is allowed
    short_min: int          # min bars before the reduced model is allowed
    zmin_full: int          # causal burn-in for the full model
    zmin_reduced: int       # causal burn-in for the reduced model
    stretch_full: int       # Mayer moving-average window, full model
    stretch_reduced: int    # Mayer moving-average window, reduced model
    vol_window: int         # realized-volatility lookback
    rsi_period: int = 14    # Wilder RSI period (bar-count; same across TFs)
    volume_window: int = 50  # volume-spike lookback of the conviction layer


# Daily = the exact historical literals (built from the module constants so any
# external importer of them stays in lockstep). analyze(df) resolves to this.
DAILY = Windows(
    smooth_span=SMOOTH_SPAN, full_min=FULL_MIN, short_min=SHORT_MIN,
    zmin_full=ZMIN_FULL, zmin_reduced=ZMIN_REDUCED,
    stretch_full=200, stretch_reduced=20, vol_window=20, rsi_period=14,
    volume_window=50,
)

# Weekly = same calendar horizons at ~5 trading days/week: MA200d->40w,
# vol20d->4w, causal year 252d->52w, reduced warm-up 20d->4w, smooth 7d(~1.4w)->2w.
# RSI stays 14 bars (a period, not a horizon). The full model needs
# full_min+zmin_full = 92 weekly bars (~1.8y) before it prints.
WEEKLY = Windows(
    smooth_span=2, full_min=40, short_min=6,
    zmin_full=52, zmin_reduced=4,
    stretch_full=40, stretch_reduced=4, vol_window=4, rsi_period=14,
    volume_window=10,
)


@dataclass
class Summary:
    date: pd.Timestamp
    close: float
    model: str
    score_raw: float
    score: float
    zone: Optional[int]
    zone_name: Optional[str]
    dwell: int
    stretch: float
    rsi: float
    drawdown: float
    trend_dev: float
    volatility: float
    conviction: Optional[str]
    conviction_label: Optional[str]
    climax: float
    vol_pct: float
    volu_pct: float
    verdict: str


def _score_core(df: pd.DataFrame, vol_weight: float, causal: bool,
                w: Windows) -> tuple[pd.DataFrame, str, pd.Series, int]:
    """The score legs and nothing else: `(frame, model, close, zmin)`.

    Extraído de `analyze()` para que la inversión de precio pueda re-puntuar sin
    pagar la clasificación de zonas ni la capa de convicción, que descarta. No
    se duplica: `analyze()` construye el resto ENCIMA de esto, así que la
    selección de modelo y los pesos siguen teniendo un solo sitio donde vivir.
    """
    out = df.reset_index(drop=True).copy()
    close = out["close"].astype(float)
    n = len(close)

    rsi = rsi_wilder(close, w.rsi_period)
    nan = pd.Series(np.nan, index=close.index)
    # Causal normalization needs its own burn-in ON TOP of the indicator's
    # window (MA200 + a year of Mayer values before the first z-score). An
    # asset with 200-451 rows genuinely cannot support a causal full model, so
    # it drops to the reduced one instead of printing an all-NaN score.
    full_min = w.full_min + w.zmin_full if causal else w.full_min
    short_min = w.short_min + w.zmin_reduced if causal else w.short_min
    zmin = w.zmin_full if n >= full_min else w.zmin_reduced

    if n < short_min:
        model = "none"
        stretch = dd = td = vol = nan
        score_raw = nan
    elif n < full_min:
        model = "reduced"
        stretch = stretch_score(close, w.stretch_reduced, causal, zmin)
        dd = td = vol = nan
        score_raw = W_REDUCED["stretch"] * stretch + W_REDUCED["rsi"] * rsi
    else:
        model = "full"
        W = _full_weights(vol_weight)
        stretch = stretch_score(close, w.stretch_full, causal, zmin)
        dd = drawdown_score(close, causal, zmin)
        td = trend_dev_score(close, causal, zmin)
        vol = volatility_score(close, w.vol_window, causal, zmin)
        score_raw = (W["stretch"] * stretch + W["rsi"] * rsi
                     + W["drawdown"] * dd + W["trend_dev"] * td
                     + W["vol"] * vol)

    out["stretch"] = stretch
    out["rsi"] = rsi
    out["drawdown"] = dd
    out["trend_dev"] = td
    out["volatility"] = vol
    out["score_raw"] = score_raw
    out["score"] = ema(score_raw, w.smooth_span)
    out["model"] = model
    return out, model, close, zmin


def score_components(df: pd.DataFrame, vol_weight: float = VOL_W_DEFAULT,
                     causal: bool = True,
                     windows: Optional[Windows] = None) -> pd.DataFrame:
    """Sólo las patas del score: sin zonas, sin convicción, sin `Summary`.

    La inversión de precio re-puntúa la MISMA historia decenas de veces por
    petición y lee únicamente estas columnas. Calcular para cada una de esas
    pasadas la clasificación por histéresis y la capa de convicción era el ~48%
    del coste de una llamada cuyo resultado se tiraba entero.

    Devuelve las mismas columnas que `analyze()` para esas patas, con los mismos
    valores: es el mismo código, no una reimplementación.
    """
    out, _, _, _ = _score_core(df, vol_weight, causal, windows or DAILY)
    return out


def analyze(df: pd.DataFrame, hysteresis: float = C.HYSTERESIS,
            vol_weight: float = VOL_W_DEFAULT,
            causal: bool = True,
            windows: Optional[Windows] = None) -> tuple[pd.DataFrame, Summary]:
    """Return (enriched frame, latest-row Summary).

    `df` must have columns 'date' and 'close' (extra columns are preserved).
    The enriched frame adds: stretch, rsi, drawdown, trend_dev, score_raw,
    score, zone, zone_name — enough to drive the dashboard tooltip.

    `causal=True` (default) normalizes every component against [0..t] only, so
    a past point on the chart is what the index actually said that day. Pass
    False to reproduce the legacy in-sample (revisionist) curve.

    `windows` selects the bar-count horizons: `None` -> `DAILY` (byte-identical
    to before this parameter existed). Pass `WEEKLY` together with a weekly-
    resampled `df` to score the same asset on weekly bars.
    """
    w = windows or DAILY
    out, model, close, zmin = _score_core(df, vol_weight, causal, w)
    score = out["score"]
    n = len(close)
    zones = C.classify_series(score, hysteresis) if model != "none" else [None] * n

    volume = out["volume"] if "volume" in out.columns else None
    # las ventanas de la capa de convicción también salen del preset: si no,
    # el clímax semanal se calculaba con lookbacks diarios (20 y 50 barras).
    conv = CV.compute(close, volume, zones, causal, zmin,
                      vol_w=w.vol_window, volu_w=w.volume_window)

    out["zone"] = zones
    out["zone_name"] = [C.NAMES[z] if z is not None else None for z in zones]
    out["vol_pct"] = conv["vol_pct"]
    out["volu_pct"] = conv["volu_pct"]
    out["climax"] = conv["climax"]
    out["conviction"] = conv["conviction"]

    summary = _summarize(out, zones, model)
    return out, summary


def _summarize(out: pd.DataFrame, zones, model: str) -> Summary:
    last = out.iloc[-1]
    zone = zones[-1] if zones else None
    dwell = C.dwell_days(zones)
    v = C.verdict(zone, stretch=last["stretch"], rsi=last["rsi"],
                  drawdown=last["drawdown"], trend_dev=last["trend_dev"], dwell=dwell)
    level = last["conviction"]
    return Summary(
        date=last["date"], close=float(last["close"]), model=model,
        score_raw=_f(last["score_raw"]), score=_f(last["score"]),
        zone=zone, zone_name=(C.NAMES[zone] if zone is not None else None),
        dwell=dwell, stretch=_f(last["stretch"]), rsi=_f(last["rsi"]),
        drawdown=_f(last["drawdown"]), trend_dev=_f(last["trend_dev"]),
        volatility=_f(last["volatility"]), conviction=level,
        conviction_label=CV.label(level), climax=_f(last["climax"]),
        vol_pct=_f(last["vol_pct"]), volu_pct=_f(last["volu_pct"]), verdict=v)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")
