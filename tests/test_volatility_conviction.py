"""Tests for the volatility leg and the conviction (confirmation) layer."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zones.indicators import realized_vol, volatility_score, volume_spike
from zones.conviction import _level, compute, label
from zones.engine import analyze


def test_volatility_score_orientation():
    # First half calm, second half violent -> calm scores HIGH, violent LOW.
    rng = np.random.default_rng(0)
    calm = 100 + np.cumsum(np.full(200, 0.02))
    violent = calm[-1] + np.cumsum(rng.normal(0, 3.0, 200))
    vs = volatility_score(pd.Series(np.concatenate([calm, violent])))
    assert vs.iloc[150] > vs.iloc[350]           # complacency > panic
    assert vs.dropna().min() >= 0 and vs.dropna().max() <= 100


def test_realized_vol_positive():
    rng = np.random.default_rng(1)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 100))))
    assert (realized_vol(close).dropna() >= 0).all()


def test_volume_spike_relative():
    v = pd.Series([100.0] * 60 + [400.0])         # a spike at the end
    vs = volume_spike(v, 50)
    assert vs.iloc[-1] > 3.0                       # ~4x its own average


def test_conviction_levels():
    assert _level(80, 0) == "alta"                 # extreme zone + climactic
    assert _level(50, 4) == "media"
    assert _level(20, 1) == "baja"
    assert _level(80, 2) is None                   # Equilibrio: not applicable
    assert _level(80, None) is None
    assert _level(float("nan"), 0) is None
    assert label("alta") == "Clímax confirmado"
    assert label(None) is None


def test_conviction_without_volume_uses_vol_only():
    rng = np.random.default_rng(2)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 300)))
    zones = [0] * 300
    c = compute(close, None, zones)
    assert c["volu_pct"].isna().all()
    # climax falls back to vol_pct where the latter is defined
    fin = c["vol_pct"].notna()
    assert np.allclose(c["climax"][fin].to_numpy(), c["vol_pct"][fin].to_numpy(), equal_nan=True)


def test_engine_full_has_volatility_leg():
    # Long enough to clear the causal burn-in (MA200 + a year of z-score history).
    n = 600
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="utc")
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    frame, s = analyze(pd.DataFrame({"date": idx, "close": close}))
    assert s.model == "full"
    assert np.isfinite(s.volatility)
    assert frame["score"].dropna().between(0, 100).all()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
