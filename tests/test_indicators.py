"""Tests for the pure normalization + indicator layer (no network)."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zones.indicators import drawdown_score, rsi_wilder, stretch_score, trend_dev_score
from zones.normalize import pct_rank, z_to_100


def test_z_to_100_bounds_and_center():
    x = pd.Series(np.linspace(1.0, 5.0, 500))
    s = z_to_100(x)
    assert s.min() >= 0.0 and s.max() <= 100.0
    # The middle of a symmetric range maps near 50.
    assert abs(s.iloc[len(s) // 2] - 50.0) < 5.0
    # Monotonic: higher raw value -> higher score.
    assert s.iloc[-1] > s.iloc[0]


def test_z_to_100_preserves_nan_and_degenerate():
    x = pd.Series([np.nan, np.nan, 1.0, 1.0, 1.0])  # zero variance among finite
    s = z_to_100(x)
    assert np.isnan(s.iloc[0]) and np.isnan(s.iloc[1])
    assert s.iloc[2] == 50.0  # degenerate -> neutral


def test_pct_rank_range():
    x = pd.Series([-0.5, -0.3, -0.1, 0.0])
    r = pct_rank(x)
    assert r.iloc[-1] == 100.0          # largest (closest to 0) -> top
    assert r.iloc[0] == 25.0            # smallest -> bottom quartile


def test_rsi_bounds_and_direction():
    up = pd.Series(np.arange(1, 200, dtype=float))      # strictly rising
    down = pd.Series(np.arange(200, 1, -1, dtype=float))  # strictly falling
    r_up, r_down = rsi_wilder(up), rsi_wilder(down)
    assert (r_up.dropna() <= 100.0).all() and (r_up.dropna() >= 0.0).all()
    assert r_up.iloc[-1] > 90.0    # pure gains -> ~100
    assert r_down.iloc[-1] < 10.0  # pure losses -> ~0


def test_stretch_direction():
    # Price ramps far above its MA20 -> stretch high at the end.
    close = pd.Series(np.concatenate([np.full(30, 100.0), np.linspace(100, 300, 30)]))
    s = stretch_score(close, 20)
    assert s.iloc[-1] > s.dropna().iloc[0]


def test_drawdown_direction():
    close = pd.Series(np.concatenate([np.linspace(100, 200, 100),   # new highs
                                      np.linspace(200, 120, 50)]))   # deep pullback
    d = drawdown_score(close)
    assert d.iloc[99] > d.iloc[-1]     # at highs > in the drawdown
    assert d.min() >= 0.0 and d.max() <= 100.0


def test_trend_dev_direction():
    t = np.arange(300)
    trend = np.exp(0.01 * t)                       # clean log-linear uptrend
    close = pd.Series(trend * (1 + 0.0 * t))
    bumped = close.copy()
    bumped.iloc[-1] *= 1.5                          # spike above trend
    td = trend_dev_score(bumped)
    assert td.iloc[-1] > 50.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
