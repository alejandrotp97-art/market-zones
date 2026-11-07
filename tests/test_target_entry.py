"""Primitives of the target-entry study (research/target_entry).

It scores the target level against the price at which the asset REALLY entered
the zone later on, so the entry detection must look only forward from the
as-of, and the M1/M3 mix weight must be fitted only on entries already seen.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.target_entry.target_entry import (
    first_entry,
    fit_weight,
    mix,
    walkforward_mix,
)


def test_first_entry_buy_and_sell_look_forward_only():
    score = np.array([10.0, 50, 40, 25, 19, 30, 85])
    assert first_entry(score, 1, 10, 20.0, "buy") == 4      # bar 0 (<=20) is the past
    assert first_entry(score, 1, 10, 80.0, "sell") == 6
    assert first_entry(score, 1, 3, 20.0, "buy") == -1      # window [1, 4) misses bar 4
    assert first_entry(np.array([50.0, np.nan, 15]), 0, 5, 20.0, "buy") == 2


def test_mix_is_geometric():
    assert abs(mix(100.0, 400.0, 0.5) - 200.0) < 1e-9
    assert abs(mix(100.0, 400.0, 1.0) - 100.0) < 1e-9
    assert abs(mix(100.0, 400.0, 0.0) - 400.0) < 1e-9


def test_fit_weight_picks_the_component_that_matches_entries():
    m1 = np.array([100.0, 100, 100])
    m3 = np.array([200.0, 200, 200])
    assert fit_weight(m1, m3, np.array([100.0, 101, 99])) == 1.0
    assert fit_weight(m1, m3, np.array([200.0, 199, 201])) == 0.0


def test_walkforward_mix_trains_only_on_entries_before_the_year():
    rows = pd.DataFrame({
        "date": pd.to_datetime(["2000-01-01", "2000-06-01", "2001-03-01", "2002-02-01"]),
        "entry_date": pd.to_datetime(["2000-05-01", "2000-09-01", "2002-06-01", "2002-08-01"]),
        "m1": [100.0, 100.0, 100.0, 100.0],
        "m3": [200.0, 200.0, 200.0, 200.0],
        "entry": [100.0, 100.0, 200.0, 150.0],
    })
    out = walkforward_mix(rows, min_train=2)
    # 2002 test: trains on entries before 2002-01-01 -> the two 2000 rows (w=1);
    # the 2001 row entered in 2002-06 and must NOT be used.
    t = out[out["date"] == "2002-02-01"].iloc[0]
    assert t["w"] == 1.0 and abs(t["pred_mix"] - 100.0) < 1e-9
    assert set(out["date"].dt.year) == {2001, 2002}


def test_band_rows_measure_depth_from_the_chosen_target_on_touches_only():
    from research.target_entry.target_entry import band_rows
    df = pd.DataFrame({
        "side": ["buy", "buy", "sell", "sell"],
        "m1": [100.0, 100.0, 200.0, 200.0], "cons": [90.0, 90.0, 210.0, 210.0],
        "fmin": [80.0, 120.0, 0.0, 0.0], "fmax": [0.0, 0.0, 250.0, 190.0],
        "vol": [0.3, 0.3, 0.3, 0.3],
    })
    b = band_rows(df, "m1", "buy")
    assert len(b) == 1 and abs(b["depth"].iloc[0] - 0.20) < 1e-12
    b = band_rows(df, "cons", "buy")
    assert abs(b["depth"].iloc[0] - (90 - 80) / 90) < 1e-12
    s = band_rows(df, "m1", "sell")
    assert len(s) == 1 and abs(s["depth"].iloc[0] - 0.25) < 1e-12
