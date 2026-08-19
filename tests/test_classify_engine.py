"""Tests for hysteresis classification and the engine's model selection."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zones.classify import classify_series, dwell_days, raw_zone
from zones.engine import analyze


def test_raw_zone_boundaries():
    assert raw_zone(0) == 0 and raw_zone(19.9) == 0
    assert raw_zone(20) == 1 and raw_zone(39.9) == 1
    assert raw_zone(60) == 3
    assert raw_zone(80) == 4 and raw_zone(100) == 4


def test_hysteresis_no_flip_within_margin():
    # Sits just above 20 then dips to 19 (within the 2-pt margin) -> stays in zone 1.
    score = pd.Series([25.0, 21.0, 19.0, 21.0])
    z = classify_series(score, hysteresis=2.0)
    assert z == [1, 1, 1, 1]


def test_hysteresis_flips_on_overshoot():
    # Drop below 20 - 2 = 18 forces the move down to zone 0.
    score = pd.Series([25.0, 17.0])
    z = classify_series(score, hysteresis=2.0)
    assert z == [1, 0]


def test_hysteresis_multi_zone_jump():
    # From deep capitulation straight into euphoria territory.
    score = pd.Series([10.0, 95.0])
    z = classify_series(score, hysteresis=2.0)
    assert z == [0, 4]


def test_classify_nan_is_none():
    score = pd.Series([np.nan, np.nan, 50.0])
    z = classify_series(score)
    assert z[0] is None and z[1] is None and z[2] == 2


def test_dwell_days():
    assert dwell_days([0, 1, 1, 1]) == 3
    assert dwell_days([None]) == 0


def _frame(closes):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="D", tz="utc")
    return pd.DataFrame({"date": idx, "close": np.asarray(closes, dtype=float)})


def test_model_selection():
    # Legacy in-sample thresholds: 30 / 200 rows.
    ins = {"causal": False}
    assert analyze(_frame(np.linspace(1, 2, 20)), **ins)[1].model == "none"
    assert analyze(_frame(np.linspace(1, 2, 100)), **ins)[1].model == "reduced"
    assert analyze(_frame(np.linspace(1, 2, 250)), **ins)[1].model == "full"
    # Causal path (the default) needs the normalization burn-in ON TOP of the
    # indicator window, so the same 250 rows can only support the reduced model.
    assert analyze(_frame(np.linspace(1, 2, 20)))[1].model == "none"
    assert analyze(_frame(np.linspace(1, 2, 250)))[1].model == "reduced"
    assert analyze(_frame(np.linspace(1, 2, 600)))[1].model == "full"


def test_very_short_has_no_zone():
    _, s = analyze(_frame(np.linspace(1, 2, 20)))
    assert s.zone is None and s.zone_name is None


def test_score_within_bounds_full_model():
    rng = np.random.default_rng(0)
    walk = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    frame, _ = analyze(_frame(walk))
    sc = frame["score"].dropna()
    assert sc.min() >= 0.0 and sc.max() <= 100.0


def test_direction_crash_lands_low():
    # Long uptrend then a hard crash: the bottom must score below the peak.
    up = np.linspace(100, 400, 250)
    crash = np.linspace(400, 240, 60)
    frame, s = analyze(_frame(np.concatenate([up, crash])))
    peak_score = frame["score"].iloc[249]
    assert s.score < peak_score
    assert s.zone <= 2  # capitulation / accumulation / equilibrium, not euphoria


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
