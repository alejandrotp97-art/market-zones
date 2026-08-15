"""Conditional-analytics correctness: past-only analogues, correct forward math."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from regime import analyze
from regime.analogs import _analog_indices, _forward, conditional_stats


def _frame(closes):
    idx = pd.date_range("2000-01-01", periods=len(closes), freq="D", tz="utc")
    return pd.DataFrame({"date": idx, "close": np.asarray(closes, dtype=float)})


def test_forward_return_is_correct():
    c = 100.0 * (1.001 ** np.arange(400))          # geometric growth
    fret, fdd, _ = _forward(pd.Series(c))
    assert np.isclose(fret[5][0], 1.001 ** 5 - 1)  # 5-day forward return
    assert np.isclose(fret[21][10], 1.001 ** 21 - 1)
    assert fdd[5][0] >= 0 or np.isclose(fdd[5][0], 1.001 - 1)  # rising -> ~no drawdown


def test_analogues_are_past_only():
    rng = np.random.default_rng(4)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 700)))
    frame, _ = analyze(_frame(close), min_periods=60)
    q = len(frame) - 1
    for method in ("regime", "knn"):
        idx = _analog_indices(frame, q, method, 80)
        assert (idx < q).all()                     # never uses the present/future


def test_conditional_stats_structure():
    rng = np.random.default_rng(5)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, 900)))
    frame, _ = analyze(_frame(close), min_periods=60)
    st = conditional_stats(frame, method="knn", k=120)
    assert st["n_analogs"] > 0
    for h in st["horizons"].values():
        if "median" in h:
            assert np.isfinite(h["median"]) and h["ci_lo"] <= h["ci_hi"]
            assert h["p10"] <= h["median"] <= h["p90"]


def test_empty_analog_set_is_integer_indexed():
    """Regression: `np.array([])` is float64, and a float array cannot index.
    Reachable whenever today's regime has never occurred before — it used to
    take the whole panel down with an IndexError."""
    import pandas as pd
    from regime.analogs import _analog_indices, conditional_stats
    n = 400
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({
        "close": 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))),
        "score": np.linspace(0, 100, n), "vol_p": np.linspace(0, 100, n),
        "dd_p": np.linspace(0, 100, n), "instab_p": np.linspace(0, 100, n),
        "regime": ["A"] * (n - 1) + ["NUNCA_VISTO"]})
    idx = _analog_indices(frame, n - 1, "regime", 250)
    assert idx.dtype.kind == "i" and len(idx) == 0
    out = conditional_stats(frame, method="regime")     # must not raise
    assert out["n_analogs"] == 0


def test_horizon_needs_closed_forward_window():
    """An analogue whose forward window had not closed at q would answer 'what
    happened next?' with prices that, on day q, had not happened yet."""
    import pandas as pd
    from regime.analogs import HORIZONS, conditional_stats
    n = 900
    rng = np.random.default_rng(1)
    frame = pd.DataFrame({
        "close": 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))),
        "score": np.full(n, 50.0), "vol_p": np.full(n, 50.0),
        "dd_p": np.full(n, 50.0), "instab_p": np.full(n, 50.0),
        "regime": ["A"] * n})
    q = 600
    out = conditional_stats(frame, query=q, method="regime")
    for name, h in HORIZONS.items():
        got = out["horizons"][name].get("n", 0)
        # Admissible analogues are i < q with i + h <= q, i.e. i in [0, q-h]:
        # inclusive, because the price at i+h IS known once day q has closed.
        cap = max(0, q - h + 1)
        assert got <= cap, f"{name}: {got} analogues, cap {cap}"
        # and the guard must actually bite at the long end
        if h > 1:
            assert out["horizons"]["12m"].get("n", 0) <= q - HORIZONS["12m"] + 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f(); print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
