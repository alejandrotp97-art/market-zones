"""Engine-level no-lookahead proof + score direction + regime logic."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from regime import analyze
from regime.regimes import (ALCISTA, CAPITULACION, CLIMAX, LATERAL, PANICO,
                           classify_series, raw_regime)


def _frame(closes):
    idx = pd.date_range("2000-01-01", periods=len(closes), freq="D", tz="utc")
    return pd.DataFrame({"date": idx, "close": np.asarray(closes, dtype=float)})


def test_engine_is_causal():
    # score[t] on the full history == score[t] on history truncated at t.
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 720)))
    df = _frame(close)
    full, _ = analyze(df, min_periods=60)
    for t in (250, 400, 560, 719):
        sub, _ = analyze(df.iloc[: t + 1], min_periods=60)
        a, b = full["score"].iloc[t], sub["score"].iloc[t]
        assert (np.isnan(a) and np.isnan(b)) or np.isclose(a, b, atol=1e-9)


def test_score_bounds_and_direction():
    rng = np.random.default_rng(3)
    up = np.linspace(100, 400, 500)
    crash = np.linspace(400, 240, 120)
    frame, s = analyze(_frame(np.concatenate([up, crash])), min_periods=60)
    sc = frame["score"].dropna()
    assert sc.min() >= 0 and sc.max() <= 100
    peak = frame["score"].iloc[499]
    assert s.score < peak                       # crash bottom cheaper than peak


def test_raw_regime_regions():
    assert raw_regime(10, 90, 20, 0) == PANICO          # cheap + vol spike
    assert raw_regime(90, 85, 20, 1) == CLIMAX          # extreme + vol spike
    assert raw_regime(60, 30, 20, 1) == ALCISTA         # mid-high, up, calm
    assert raw_regime(10, 30, 20, 0) == CAPITULACION    # very cheap, calm-ish
    assert raw_regime(50, 30, 80, 1) == LATERAL         # choppy tape
    assert raw_regime(np.nan, 1, 1, 1) is None


def test_hysteresis_dwell():
    # A single-day blip must NOT switch a non-shock regime (dwell=5)...
    L = np.array([60.0] * 10 + [45.0] + [60.0] * 5)
    V = np.full(len(L), 30.0); I = np.full(len(L), 20.0); up = np.ones(len(L))
    z = classify_series(L, V, I, up, dwell=5)
    assert len(set(z)) == 1 and z[-1] == ALCISTA
    # ...but a shock switches immediately.
    L2 = np.array([60.0] * 8 + [10.0])
    V2 = np.array([30.0] * 8 + [90.0])
    z2 = classify_series(L2, V2, np.full(9, 20.0), np.ones(9), dwell=5)
    assert z2[-1] == PANICO


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f(); print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
