"""The validated indicator set (FASE 1-3). Raw values, all causal.

Score core (3):  mayer · realized_vol · drawdown
Regime-only:     sma200_crosses (instability) · sma200_slope_up (trend direction)

Every function uses only rolling/expanding windows, so indicator(t) depends
only on prices up to t.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SMA = 200


def sma200(close: pd.Series) -> pd.Series:
    return pd.Series(close, dtype=float).rolling(SMA).mean()


def mayer(close: pd.Series) -> pd.Series:
    """Mayer Multiple: price / SMA200. Extension axis (FASE 3 primary)."""
    return pd.Series(close, dtype=float) / sma200(close)


def realized_vol(close: pd.Series, w: int = 20) -> pd.Series:
    """Annualized realized volatility. Volatility axis (most informative)."""
    return np.log(pd.Series(close, dtype=float)).diff().rolling(w).std() * np.sqrt(252.0)


def drawdown(close: pd.Series) -> pd.Series:
    """Drawdown from the running all-time high. Cycle-position axis."""
    c = pd.Series(close, dtype=float)
    return c / c.cummax() - 1.0


def sma200_crosses(close: pd.Series, w: int = 120) -> pd.Series:
    """Number of SMA200 crossings in the last `w` days. Instability axis
    (regime FSM only — NOT a Score input, per FASE 1)."""
    c = pd.Series(close, dtype=float)
    sign = np.sign((c - sma200(c)).fillna(0.0))
    return (sign.diff().abs() > 0).astype(float).rolling(w).sum()


def sma200_slope_up(close: pd.Series, w: int = 20) -> pd.Series:
    """Boolean-ish (0/1): is the SMA200 rising over the last `w` days? Trend
    DIRECTION for regime labeling only (slope magnitude is redundant per FASE 1,
    but its sign is needed to orient the state machine)."""
    return (sma200(close).diff(w) > 0).astype(float)
