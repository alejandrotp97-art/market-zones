"""The no-lookahead guarantee, proven at the normalizer level."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from regime.normalize import expanding_percentile


def test_causality_exact():
    # The value at t computed on the full series must EXACTLY equal the value
    # computed on the series truncated at t. This is what the legacy in-sample
    # z-score fails: it uses future data to normalize the past.
    rng = np.random.default_rng(0)
    x = rng.normal(size=500)
    full = expanding_percentile(x, min_periods=20)
    for t in (25, 80, 199, 350, 499):
        trunc = expanding_percentile(x[: t + 1], min_periods=20)
        a, b = full[t], trunc[t]
        assert (np.isnan(a) and np.isnan(b)) or a == b


def test_bounds_and_burn_in():
    rng = np.random.default_rng(1)
    x = rng.normal(size=300)
    p = expanding_percentile(x, min_periods=50)
    assert np.all(np.isnan(p[:49]))              # burn-in
    fin = p[np.isfinite(p)]
    assert fin.min() > 0 and fin.max() <= 100


def test_monotone_last_value():
    # A new all-time-high maps to ~100; a new all-time-low to ~ low percentile.
    x = np.concatenate([np.linspace(0, 1, 100), [5.0]])   # spike high
    p = expanding_percentile(x, min_periods=20)
    assert p[-1] > 99
    x2 = np.concatenate([np.linspace(0, 1, 100), [-5.0]])  # spike low
    p2 = expanding_percentile(x2, min_periods=20)
    assert p2[-1] < 1


def test_nan_skipped():
    x = np.array([1.0, np.nan, 2.0, 3.0, np.nan, 4.0])
    p = expanding_percentile(x, min_periods=1)
    assert np.isnan(p[1]) and np.isnan(p[4])     # gaps stay NaN
    assert np.isfinite(p[0]) and np.isfinite(p[5])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f(); print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
