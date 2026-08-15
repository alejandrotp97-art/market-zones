"""The no-lookahead guarantee for the zones engine.

Two properties are asserted here, and together they are the whole point of the
causal path:

  1. TRUTHFUL HISTORY — the value at t computed on the full series equals the
     value computed on the series truncated at t. The in-sample path fails this
     by construction; that failure is what made the chart revisionist.
  2. NO FREE LUNCH — for stretch/drawdown/volatility the causal value on the
     LAST row is identical to the in-sample one, because [0..t] is the whole
     series there. Switching to causal fixes the past without moving today.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zones.engine import ZMIN_FULL, analyze
from zones.indicators import (drawdown_score, stretch_score, trend_dev_score,
                              volatility_score)
from zones.normalize import (expanding_pct_rank, expanding_z_to_100, pct_rank,
                             z_to_100)


def _series(n=2000, seed=5):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))
    return pd.DataFrame({"date": pd.bdate_range("2010-01-01", periods=n),
                         "close": close,
                         "volume": rng.integers(1e6, 5e6, n)})


def test_expanding_z_is_causal():
    rng = np.random.default_rng(0)
    x = pd.Series(rng.normal(size=1200))
    full = expanding_z_to_100(x, 100)
    for t in (150, 400, 900, 1199):
        trunc = expanding_z_to_100(x.iloc[: t + 1], 100)
        assert abs(full.iloc[t] - trunc.iloc[t]) < 1e-12


def test_expanding_pct_rank_is_causal():
    rng = np.random.default_rng(1)
    x = pd.Series(rng.normal(size=800))
    full = expanding_pct_rank(x)
    for t in (10, 250, 799):
        trunc = expanding_pct_rank(x.iloc[: t + 1])
        assert abs(full.iloc[t] - trunc.iloc[t]) < 1e-12


def test_expanding_matches_in_sample_on_last_row():
    # [0..t] IS the whole series at t = n-1, so the two must coincide exactly.
    rng = np.random.default_rng(2)
    x = pd.Series(rng.normal(size=900))
    assert abs(expanding_z_to_100(x, 100).iloc[-1] - z_to_100(x).iloc[-1]) < 1e-9
    assert abs(expanding_pct_rank(x).iloc[-1] - pct_rank(x).iloc[-1]) < 1e-9


def test_expanding_z_survives_large_offsets():
    """Regression: accumulating sums of squares around 0 cancels catastrophically
    when values are large relative to their spread. A series centred on 1e9 used
    to come out 4.5 SCORE POINTS off. No current indicator lives up there — this
    guards the next one that does."""
    rng = np.random.default_rng(11)
    for scale in (1.0, 1e3, 1e6, 1e9):
        x = pd.Series(rng.normal(size=2000) + scale)
        a = float(expanding_z_to_100(x, 252).iloc[-1])
        b = float(z_to_100(x).iloc[-1])
        assert abs(a - b) < 1e-5, f"drift {abs(a - b):.2e} at scale {scale:.0e}"


def test_expanding_z_burn_in_and_degenerate():
    x = pd.Series([np.nan] * 5 + [1.0] * 60)          # zero variance among finite
    s = expanding_z_to_100(x, 20)
    assert np.isnan(s.iloc[:24]).all()                 # burn-in (5 NaN + 20 finite)
    assert s.iloc[-1] == 50.0                          # degenerate -> neutral


def test_causal_components_do_not_move_today():
    close = _series()["close"]
    for fn, kw in ((stretch_score, {"ma_window": 200}), (drawdown_score, {}),
                   (volatility_score, {})):
        a = float(fn(close, causal=True, **kw).iloc[-1])
        b = float(fn(close, causal=False, **kw).iloc[-1])
        assert abs(a - b) < 1e-9, f"{fn.__name__} moved today's reading"


def test_trend_dev_expanding_fit_matches_polyfit_on_last_row():
    # The expanding OLS at t = n-1 spans the whole sample, so the RESIDUAL must
    # match np.polyfit exactly (the 0-100 score differs only because the z
    # reference set is now causal).
    from zones.indicators import _expanding_trend_resid
    close = _series()["close"]
    lc = np.log(close.to_numpy())
    t = np.arange(len(lc), dtype=float)
    b, a = np.polyfit(t, lc, 1)
    expected = lc[-1] - (a + b * t[-1])
    assert abs(_expanding_trend_resid(lc)[-1] - expected) < 1e-9


def test_engine_history_is_stable_under_truncation():
    """The headline property: what the panel shows for a past date must equal
    what it would have shown ON that date. This is the assertion the in-sample
    engine cannot pass."""
    df = _series()
    full, _ = analyze(df, causal=True)
    for cut in (900, 1400, 1900):
        part, _ = analyze(df.iloc[: cut + 1].reset_index(drop=True), causal=True)
        a, b = full["score"].iloc[cut], part["score"].iloc[cut]
        assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9, f"drift at {cut}"
        assert full["zone_name"].iloc[cut] == part["zone_name"].iloc[cut]


def test_in_sample_engine_still_drifts():
    """Guard the guard: if this ever stops failing, the A/B path is broken and
    `test_engine_history_is_stable_under_truncation` proves nothing."""
    df = _series()
    full, _ = analyze(df, causal=False)
    drift = 0.0
    for cut in (900, 1400, 1900):
        part, _ = analyze(df.iloc[: cut + 1].reset_index(drop=True), causal=False)
        a, b = full["score"].iloc[cut], part["score"].iloc[cut]
        if np.isfinite(a) and np.isfinite(b):
            drift = max(drift, abs(a - b))
    assert drift > 1.0, "in-sample path unexpectedly causal — A/B is meaningless"


def test_short_history_falls_back_instead_of_going_blank():
    # 300 rows cannot support a causal MA200 + 252-row burn-in, so the engine
    # must drop to the reduced model rather than return an all-NaN score.
    df = _series(n=300)
    frame, s = analyze(df, causal=True)
    assert s.model == "reduced"
    assert np.isfinite(s.score) and s.zone is not None
    assert frame["score"].notna().sum() > 0


def test_full_model_engages_once_burn_in_is_covered():
    df = _series(n=200 + ZMIN_FULL + 10)
    _, s = analyze(df, causal=True)
    assert s.model == "full" and np.isfinite(s.score)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
