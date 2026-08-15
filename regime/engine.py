"""Minimal regime engine (FASE 4 spec). Causal by construction.

Score = equal-weight mean of the expanding-percentile of the 3 validated axes,
with volatility inverted (high vol = panic = cheap = low score):

    score = mean( P(mayer), 100 - P(rvol), P(drawdown) )

Regime = region state machine over (level=score, vol, instability, trend).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as I
from . import regimes as R
from .normalize import expanding_percentile

BURN_IN = 504


@dataclass
class Reading:
    date: pd.Timestamp
    close: float
    score: float
    regime: Optional[str]
    dwell: int
    level: float          # = score
    extension: float      # P(mayer) — raw extension score input
    vol: float            # P(rvol)
    cycle: float          # P(drawdown)
    instability: float    # P(crosses)
    trend_up: bool


def analyze(df: pd.DataFrame, min_periods: int = BURN_IN, dwell: int = 5):
    out = df.reset_index(drop=True).copy()
    close = out["close"].astype(float)

    p_may = expanding_percentile(I.mayer(close), min_periods)
    p_rv = expanding_percentile(I.realized_vol(close), min_periods)
    p_dd = expanding_percentile(I.drawdown(close), min_periods)
    p_cross = expanding_percentile(I.sma200_crosses(close), min_periods)
    up = I.sma200_slope_up(close).to_numpy()

    comp = np.vstack([p_may, 100.0 - p_rv, p_dd])       # vol inverted
    valid = np.all(np.isfinite(comp), axis=0)
    # plain mean: where valid all three are finite; elsewhere -> NaN (no warning)
    score = np.where(valid, comp.mean(axis=0), np.nan)

    regimes = R.classify_series(score, p_rv, p_cross, up, dwell)

    out["mayer_p"] = p_may
    out["vol_p"] = p_rv
    out["dd_p"] = p_dd
    out["instab_p"] = p_cross
    out["trend_up"] = up
    out["score"] = score
    out["regime"] = regimes
    return out, _summary(out, regimes)


def _summary(out: pd.DataFrame, regimes: list) -> Reading:
    last = out.iloc[-1]
    return Reading(
        date=last["date"], close=float(last["close"]),
        score=_f(last["score"]), regime=regimes[-1] if regimes else None,
        dwell=R.dwell_days(regimes), level=_f(last["score"]),
        extension=_f(last["mayer_p"]), vol=_f(last["vol_p"]), cycle=_f(last["dd_p"]),
        instability=_f(last["instab_p"]), trend_up=bool(last["trend_up"]))


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")
