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


def analyze(df: pd.DataFrame, hysteresis: float = C.HYSTERESIS,
            vol_weight: float = VOL_W_DEFAULT,
            causal: bool = True) -> tuple[pd.DataFrame, Summary]:
    """Return (enriched frame, latest-row Summary).

    `df` must have columns 'date' and 'close' (extra columns are preserved).
    The enriched frame adds: stretch, rsi, drawdown, trend_dev, score_raw,
    score, zone, zone_name — enough to drive the dashboard tooltip.

    `causal=True` (default) normalizes every component against [0..t] only, so
    a past point on the chart is what the index actually said that day. Pass
    False to reproduce the legacy in-sample (revisionist) curve.
    """
    out = df.reset_index(drop=True).copy()
    close = out["close"].astype(float)
    n = len(close)

    rsi = rsi_wilder(close)
    nan = pd.Series(np.nan, index=close.index)
    # Causal normalization needs its own burn-in ON TOP of the indicator's
    # window (MA200 + a year of Mayer values before the first z-score). An
    # asset with 200-451 rows genuinely cannot support a causal full model, so
    # it drops to the reduced one instead of printing an all-NaN score.
    full_min = FULL_MIN + ZMIN_FULL if causal else FULL_MIN
    short_min = SHORT_MIN + ZMIN_REDUCED if causal else SHORT_MIN
    zmin = ZMIN_FULL if n >= full_min else ZMIN_REDUCED

    if n < short_min:
        model = "none"
        stretch = dd = td = vol = nan
        score_raw = nan
    elif n < full_min:
        model = "reduced"
        stretch = stretch_score(close, 20, causal, zmin)
        dd = td = vol = nan
        score_raw = W_REDUCED["stretch"] * stretch + W_REDUCED["rsi"] * rsi
    else:
        model = "full"
        W = _full_weights(vol_weight)
        stretch = stretch_score(close, 200, causal, zmin)
        dd = drawdown_score(close, causal, zmin)
        td = trend_dev_score(close, causal, zmin)
        vol = volatility_score(close, 20, causal, zmin)
        score_raw = (W["stretch"] * stretch + W["rsi"] * rsi
                     + W["drawdown"] * dd + W["trend_dev"] * td
                     + W["vol"] * vol)

    score = ema(score_raw, SMOOTH_SPAN)
    zones = C.classify_series(score, hysteresis) if model != "none" else [None] * n

    volume = out["volume"] if "volume" in out.columns else None
    conv = CV.compute(close, volume, zones, causal, zmin)

    out["stretch"] = stretch
    out["rsi"] = rsi
    out["drawdown"] = dd
    out["trend_dev"] = td
    out["volatility"] = vol
    out["score_raw"] = score_raw
    out["score"] = score
    out["zone"] = zones
    out["zone_name"] = [C.NAMES[z] if z is not None else None for z in zones]
    out["vol_pct"] = conv["vol_pct"]
    out["volu_pct"] = conv["volu_pct"]
    out["climax"] = conv["climax"]
    out["conviction"] = conv["conviction"]
    out["model"] = model

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
