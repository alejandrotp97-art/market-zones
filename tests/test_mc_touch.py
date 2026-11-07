"""Core pieces of the Monte Carlo touch-probability study (research/mc_touch).

The study is only worth its validation if these primitives are right:
the block bootstrap must keep contiguous return blocks, the touch probability
must read the path extremes (not just the end point), and the calibration
table must compare predicted vs realized frequencies per bin.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.mc_touch.mc_touch import (
    bootstrap_paths,
    calibration,
    gbm_touch_prob,
    mc_touch_prob,
    pool_returns,
    realized_touch,
)


def test_pool_returns_is_causal_and_starts_at_the_pool_date():
    import pandas as pd
    dates = pd.date_range("2020-12-27", periods=10, freq="D", tz="UTC")
    close = np.exp(np.arange(10, dtype=float))      # every log return == 1
    close[3] = close[3] * 5                          # spike before the pool start
    start = pd.Timestamp("2021-01-01", tz="UTC")     # index 5
    r = pool_returns(close, dates, 8, start)
    assert np.allclose(r, [1.0, 1.0, 1.0])           # closes 5..8 only, nothing after 8
    full = pool_returns(close, dates, 8, None)
    assert len(full) == 8


def test_bootstrap_shape_and_determinism():
    r = np.linspace(-0.05, 0.05, 200)
    a = bootstrap_paths(r, n_paths=500, horizon=30, mean_block=20, seed=7)
    b = bootstrap_paths(r, n_paths=500, horizon=30, mean_block=20, seed=7)
    assert a.shape == (500, 30)
    assert np.array_equal(a, b)
    # every draw comes from the source returns
    assert np.isin(a, r).all()


def test_bootstrap_infinite_block_is_a_contiguous_circular_slice():
    r = np.arange(50, dtype=float)
    paths = bootstrap_paths(r, n_paths=100, horizon=10, mean_block=np.inf, seed=1)
    steps = np.diff(paths, axis=1) % 50
    assert (steps == 1).all()


def test_bootstrap_keeps_blocks_of_roughly_the_mean_length():
    r = np.arange(10_000, dtype=float)
    paths = bootstrap_paths(r, n_paths=2000, horizon=60, mean_block=20, seed=3)
    breaks = (np.diff(paths, axis=1) % 10_000 != 1).mean()
    # a new block starts with probability 1/mean_block per step
    assert abs(breaks - 1 / 20) < 0.005


def test_mc_touch_reads_the_path_extreme_not_the_end():
    # down 10% then fully back: ends at the start price but touched -10%
    down_up = np.log([0.9, 1 / 0.9])
    paths = np.tile(down_up, (4, 1))
    assert mc_touch_prob(100.0, 91.0, paths, side="buy") == 1.0
    assert mc_touch_prob(100.0, 89.0, paths, side="buy") == 0.0
    up_down = -down_up
    paths = np.tile(up_down, (4, 1))
    assert mc_touch_prob(100.0, 110.0, paths, side="sell") == 1.0
    assert mc_touch_prob(100.0, 112.0, paths, side="sell") == 0.0


def test_mc_touch_counts_the_fraction_of_paths():
    paths = np.array([[np.log(0.8)], [0.0], [0.0], [np.log(0.85)]])
    assert mc_touch_prob(100.0, 90.0, paths, side="buy") == 0.5


def test_target_already_inside_the_zone_is_certain():
    paths = np.zeros((3, 5))
    assert mc_touch_prob(100.0, 120.0, paths, side="buy") == 1.0
    assert mc_touch_prob(100.0, 80.0, paths, side="sell") == 1.0
    assert gbm_touch_prob(100.0, 100.0, 0.03, 30, side="buy") == 1.0


def test_gbm_touch_matches_the_reflection_formula():
    # zero-drift reflection: P(min <= b) = 2 * Phi(ln(b/S) / (sigma*sqrt(H)))
    from scipy.stats import norm
    p = gbm_touch_prob(100.0, 90.0, 0.02, 25, side="buy")
    assert abs(p - 2 * norm.cdf(np.log(0.9) / (0.02 * 5))) < 1e-12
    q = gbm_touch_prob(100.0, 110.0, 0.02, 25, side="sell")
    assert abs(q - 2 * norm.cdf(-np.log(1.1) / (0.02 * 5))) < 1e-12


def test_realized_touch_uses_closes_inside_the_window():
    fut = np.array([100.0, 95.0, 89.0, 104.0])
    assert realized_touch(fut, 90.0, side="buy") is True
    assert realized_touch(fut, 88.0, side="buy") is False
    assert realized_touch(fut, 104.0, side="sell") is True
    assert realized_touch(fut, 105.0, side="sell") is False


def test_calibration_bins_and_brier():
    p = np.array([0.05, 0.05, 0.95, 0.95])
    y = np.array([0, 0, 1, 1])
    out = calibration(p, y, bins=(0.0, 0.5, 1.0))
    assert [b["n"] for b in out["bins"]] == [2, 2]
    assert out["bins"][0]["pred"] == 0.05 and out["bins"][0]["real"] == 0.0
    assert out["bins"][1]["pred"] == 0.95 and out["bins"][1]["real"] == 1.0
    assert abs(out["brier"] - 0.0025) < 1e-12
    # base rate 0.5 -> reference Brier 0.25 -> skill 0.99
    assert abs(out["skill"] - (1 - 0.0025 / 0.25)) < 1e-12
