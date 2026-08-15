"""The four raw components of the market-zone score, plus helpers.

Every component is oriented the same way: HIGH = expensive/euphoric,
LOW = cheap/capitulation. That shared orientation is what makes the weighted
average meaningful. All functions are pure and operate on a price Series.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .normalize import (Z_MIN_PERIODS, expanding_pct_rank, expanding_z_to_100,
                        pct_rank, z_to_100)


def _scale(x: pd.Series, causal: bool, zmin: int) -> pd.Series:
    """z-score -> 0-100, causally (expanding) or in-sample."""
    return expanding_z_to_100(x, zmin) if causal else z_to_100(x)


def _rank(x: pd.Series, causal: bool, rmin: int) -> pd.Series:
    """percentile -> 0-100, causally (expanding) or in-sample."""
    return expanding_pct_rank(x, rmin) if causal else pct_rank(x)


def _expanding_trend_resid(y: np.ndarray) -> np.ndarray:
    """Residual of `y` vs a log-linear trend REFIT at every t on [0..t] only.

    Closed-form expanding OLS via cumulative sums: at each t the slope/intercept
    use only the finite observations up to t, so residual(t) is causal. At the
    last row the fit spans the whole sample, so it coincides with the in-sample
    `np.polyfit` result.
    """
    t = np.arange(len(y), dtype=float)
    fin = np.isfinite(y)
    ys, ts = np.where(fin, y, 0.0), np.where(fin, t, 0.0)
    n = np.cumsum(fin).astype(float)
    nz = np.where(n > 0, n, 1.0)
    sx, sy = np.cumsum(ts), np.cumsum(ys)
    sxx, sxy = np.cumsum(ts * ts), np.cumsum(ts * ys)
    with np.errstate(invalid="ignore", divide="ignore"):
        denom = sxx - sx * sx / nz              # 0 until two distinct t are seen
        b = (sxy - sx * sy / nz) / np.where(denom > 0.0, denom, np.nan)
        a = sy / nz - b * (sx / nz)
    out = np.full(len(y), np.nan)
    ok = fin & (n >= 2) & np.isfinite(b)
    out[ok] = y[ok] - (a[ok] + b[ok] * t[ok])
    return out


def ema(x: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (adjust=False -> classic recursive EMA)."""
    return pd.Series(x, dtype=float).ewm(span=span, adjust=False).mean()


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (RMA smoothing), already on a 0-100 scale.

    High RSI = overbought = euphoric = high component, consistent with the
    other three components.
    """
    close = pd.Series(close, dtype=float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder smoothing == EMA with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # Edge cases where the ratio is undefined.
    rsi = rsi.mask(avg_loss == 0.0, 100.0)                       # pure gains
    rsi = rsi.mask(avg_gain == 0.0, 0.0)                         # pure losses
    rsi = rsi.mask((avg_gain == 0.0) & (avg_loss == 0.0), 50.0)  # perfectly flat
    return rsi


def stretch_score(close: pd.Series, ma_window: int, causal: bool = False,
                  zmin: int = Z_MIN_PERIODS) -> pd.Series:
    """Mayer-Multiple stretch: price / moving-average, z-scored to 0-100.

    ma_window is 200 for the full model, 20 for the reduced (short-history)
    model. Undefined until the moving average has enough data. The raw ratio is
    already causal; `causal=True` makes the NORMALIZATION causal too.
    """
    close = pd.Series(close, dtype=float)
    ma = close.rolling(ma_window, min_periods=ma_window).mean()
    mayer = close / ma
    return _scale(mayer, causal, zmin)


def drawdown_score(close: pd.Series, causal: bool = False,
                   rmin: int = Z_MIN_PERIODS) -> pd.Series:
    """Drawdown from the running all-time high, expressed as a percentile of
    the asset's own drawdown history.

    dd = close/cummax - 1  (0 at highs, negative below). Deeper drawdown ->
    lower percentile -> lower score = capitulation.
    """
    close = pd.Series(close, dtype=float)
    dd = close / close.cummax() - 1.0
    return _rank(dd, causal, rmin)


def trend_dev_score(close: pd.Series, causal: bool = False,
                    zmin: int = Z_MIN_PERIODS) -> pd.Series:
    """Residual of log(price) versus its log-linear trend, z-scored to 0-100.

    Above the trend line -> high score (euphoria); below -> low (capitulation).

    `causal=False` fits log(price) = a + b*t once over the whole sample, so the
    trend line is drawn with knowledge of where the price ended and every past
    residual is contaminated. `causal=True` refits on [0..t] at every t.
    """
    close = pd.Series(close, dtype=float)
    lc = np.log(close.to_numpy())
    if np.isfinite(lc).sum() < 2:
        return pd.Series(np.nan, index=close.index)
    if causal:
        return _scale(pd.Series(_expanding_trend_resid(lc), index=close.index),
                      True, zmin)
    t = np.arange(len(lc), dtype=float)
    mask = np.isfinite(lc)
    b, a = np.polyfit(t[mask], lc[mask], 1)
    resid = lc - (a + b * t)
    return z_to_100(pd.Series(resid, index=close.index))


def realized_vol(close: pd.Series, w: int = 20) -> pd.Series:
    """Annualized realized volatility: std of daily log-returns over `w` days."""
    r = np.log(pd.Series(close, dtype=float)).diff()
    return r.rolling(w).std() * np.sqrt(252.0)


def volatility_score(close: pd.Series, w: int = 20, causal: bool = False,
                     zmin: int = Z_MIN_PERIODS) -> pd.Series:
    """Volatility regime as a 0-100 component, oriented like the others
    (high = expensive/complacent, low = cheap/panic): calm tape scores high
    (late-cycle complacency), violent tape scores low (capitulation panic).
    That inversion is what lets volatility live on the cheap<->expensive axis.
    """
    return 100.0 - _scale(realized_vol(close, w), causal, zmin)


def volume_spike(volume: pd.Series, w: int = 50) -> pd.Series:
    """Volume relative to its own `w`-day average. Self-normalized, so it is
    comparable across assets regardless of absolute share counts."""
    v = pd.Series(volume, dtype=float)
    return v / v.rolling(w, min_periods=w).mean()
