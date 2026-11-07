"""Tests for the target-price inversion (zones/target.py).

Network-free: a synthetic full-model series is scored, then inverted. The load-
bearing invariant is that the M1 price actually reproduces the boundary score
when fed back through the real engine — the inversion is only honest if it round-
trips.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import itertools

from zones.engine import analyze
from zones.target import BUY_U, SELL_U, compute, currency


def _frame(n: int = 700) -> pd.DataFrame:
    """A rising, oscillating price with enough history for the full causal model
    and visits to both extremes, so every method has something to work with."""
    t = np.arange(n, dtype=float)
    price = 100.0 * np.exp(0.0005 * t) * (1.0 + 0.18 * np.sin(t / 45.0))
    return pd.DataFrame({"date": pd.date_range("2015-01-01", periods=n, freq="D"),
                         "close": price, "volume": np.full(n, 1_000_000.0)})


def _score_raw_at(df: pd.DataFrame, price: float) -> float:
    """score_raw the engine prints if today's close were `price`."""
    d = df.reset_index(drop=True).copy()
    d.loc[len(d) - 1, "close"] = float(price)
    _, s = analyze(d)
    return float(s.score_raw)


def test_returns_expected_shape():
    df = _frame()
    t = compute(df, "TEST")
    assert t is not None and t["model"] == "full"
    for side in ("buy", "sell"):
        assert set(t[side]) >= {"u", "m1", "m2", "m3", "consensus", "pct"}
    assert t["buy"]["u"] == BUY_U and t["sell"]["u"] == SELL_U


def test_m1_round_trips_to_the_boundary():
    df = _frame()
    t = compute(df, "TEST")
    # The whole point: the price M1 hands back must READ ~20 / ~80 on the engine.
    assert abs(_score_raw_at(df, t["buy"]["m1"]) - BUY_U) < 1.0
    assert abs(_score_raw_at(df, t["sell"]["m1"]) - SELL_U) < 1.0


def test_buy_boundary_below_sell_boundary():
    df = _frame()
    t = compute(df, "TEST")
    # score_raw is (near-)monotone increasing in price, so cheaper zone -> lower price.
    assert t["buy"]["m1"] < t["sell"]["m1"]


def test_curve_is_monotone_up_to_the_vol_wiggle():
    df = _frame()
    t = compute(df, "TEST")
    ys = [p[1] for p in t["curve"]]
    # Volatility (10%, non-monotone by design) can dent one step; nothing more.
    drops = sum(1 for a, b in itertools.pairwise(ys) if b < a - 1e-6)
    assert drops <= 1, f"curve not monotone: {drops} drops"


def test_headline_target_is_m1():
    # research/target_entry: M1 alone lands closer to the price at which the
    # asset really enters the zone than the old median(M1, M3), out of sample.
    # M2 (diagnostic) and M3 (history) never move the headline while M1 exists.
    df = _frame()
    t = compute(df, "TEST")
    for side in ("buy", "sell"):
        s = t[side]
        assert s["m3"] is not None and abs(s["m3"]["mid"] - s["m1"]) > 0.5
        assert s["consensus"] == s["m1"]


def test_headline_falls_back_to_m3_when_m1_has_no_solution():
    from zones.target import _headline
    assert _headline(None, {"mid": 5.0}) == 5.0
    assert _headline(3.0, {"mid": 5.0}) == 3.0
    assert _headline(None, None) is None
    assert _headline(3.0, None) == 3.0


def test_band_hangs_from_the_headline_target():
    df = _frame()
    t = compute(df, "TEST")
    assert t["buy"]["band"] < t["buy"]["consensus"]
    assert t["sell"]["band"] > t["sell"]["consensus"]
    assert t["buy"]["pct"] == round((t["buy"]["m1"] - t["price"]) / t["price"] * 100, 1)


def test_short_history_returns_none():
    df = _frame(120)                     # below the causal full/reduced minimum path
    assert compute(df, "TEST") is None or compute(df, "TEST")["model"] == "reduced"
    assert compute(_frame(40), "TEST") is None


def test_currency_by_suffix():
    assert currency("NUKL.DE") == "€"
    assert currency("VOD.L") == "£"
    assert currency("SPY") == "$"
    assert currency("BRK-B") == "$"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all target tests passed")
