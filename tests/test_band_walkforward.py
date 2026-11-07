"""Primitives of the band walk-forward study (research/band_calibration).

The study decides whether a new band ships, so it must (1) measure vol exactly
like the panel does, (2) never train on a sample whose forward window was still
open at the test date, and (3) score with a declared loss.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research", "band_calibration"))

from band_walkforward import asof_vol, fit_k, pinball, walkforward


def test_asof_vol_matches_the_panel_formula():
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 600)))
    lr = np.diff(np.log(close))
    expected = np.std(lr[-252:], ddof=1) * np.sqrt(252)
    assert abs(asof_vol(close) - expected) < 1e-12


def test_asof_vol_drops_non_finite_returns():
    close = np.array([100.0, 101.0, np.nan, 103.0, 104.0, 105.0, 106.0, 107.0,
                      109.0, 108.0, 111.0])
    lr = np.diff(np.log(close))
    lr = lr[np.isfinite(lr)]
    assert abs(asof_vol(close) - np.std(lr, ddof=1) * np.sqrt(252)) < 1e-12


def test_pinball_median_is_half_the_absolute_error():
    pred = np.array([0.1, 0.2, 0.3])
    y = np.array([0.2, 0.2, 0.1])
    assert abs(pinball(pred, y, 0.5) - 0.5 * np.mean(np.abs(pred - y))) < 1e-12
    # q=0.8 penalises under-prediction 4x more than over-prediction
    assert abs(pinball(np.array([0.0]), np.array([1.0]), 0.8) - 0.8) < 1e-12
    assert abs(pinball(np.array([1.0]), np.array([0.0]), 0.8) - 0.2) < 1e-12


def test_fit_k_is_the_median_ratio_and_skips_bad_vol():
    depth = np.array([0.2, 0.4, 0.6, 9.0])
    vol = np.array([0.4, 0.4, 0.4, 0.0])
    assert fit_k(depth, vol) == 1.0


def _rows(spec):
    return pd.DataFrame(spec, columns=["date", "end", "cls", "depth", "vol"]).assign(
        date=lambda d: pd.to_datetime(d["date"]), end=lambda d: pd.to_datetime(d["end"]))


def test_walkforward_never_trains_on_an_open_window():
    rows = _rows([
        ("2000-01-01", "2001-06-01", "a", 0.10, 0.5),   # closed before 2002 -> trains
        ("2001-01-01", "2002-06-01", "a", 0.90, 0.5),   # still open on 2002-01-01 -> excluded
        ("2002-03-01", "2003-01-01", "a", 0.30, 0.5),   # test sample
    ])
    out = walkforward(rows, k_current=1.0, cap=None, min_train=1)
    t = out[out["date"] == "2002-03-01"].iloc[0]
    assert abs(t["pred_global"] - 0.10) < 1e-12        # k = 0.10 / 0.5 = 0.2 -> 0.2*0.5
    assert abs(t["pred_current"] - 0.50) < 1e-12


def test_walkforward_class_model_falls_back_to_global_when_thin():
    rows = _rows([
        ("2000-01-01", "2001-01-01", "a", 0.10, 0.5),
        ("2000-02-01", "2001-01-01", "a", 0.10, 0.5),
        ("2000-03-01", "2001-01-01", "b", 0.40, 0.5),
        ("2003-01-01", "2004-01-01", "b", 0.20, 0.5),
        ("2003-01-01", "2004-01-01", "c", 0.20, 0.5),
    ])
    out = walkforward(rows, k_current=1.0, cap=None, min_train=1, min_class=2)
    b = out[out["cls"] == "b"].iloc[0]
    c = out[out["cls"] == "c"].iloc[0]
    k_glob = np.median([0.2, 0.2, 0.8])
    assert abs(b["pred_class"] - k_glob * 0.5) < 1e-12   # class b has 1 < 2 samples
    assert abs(c["pred_class"] - k_glob * 0.5) < 1e-12   # class c has none


def test_walkforward_applies_the_depth_cap():
    rows = _rows([
        ("2000-01-01", "2001-01-01", "a", 2.0, 1.0),
        ("2003-01-01", "2004-01-01", "a", 0.5, 1.0),
    ])
    out = walkforward(rows, k_current=1.5, cap=0.9, min_train=1)
    t = out.iloc[0]
    assert t["pred_current"] == 0.9 and t["pred_global"] == 0.9


def test_asof_vol_window_is_separate_from_annualisation():
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 900)))
    lr = np.diff(np.log(close))
    assert abs(asof_vol(close, window=63) - np.std(lr[-63:], ddof=1) * np.sqrt(252)) < 1e-12
    assert abs(asof_vol(close, window=756) - np.std(lr[-756:], ddof=1) * np.sqrt(252)) < 1e-12


def test_fit_power_recovers_an_exact_power_law():
    from band_walkforward import fit_power
    vol = np.linspace(0.1, 1.0, 40)
    depth = 0.5 * vol ** 1.5
    a, b = fit_power(depth, vol)
    assert abs(a - 0.5) < 1e-3 and abs(b - 1.5) < 1e-3


def test_walkforward_power_column_uses_only_closed_samples():
    rows = _rows([
        ("2000-01-01", "2001-01-01", "a", 0.05, 0.1),
        ("2000-02-01", "2001-01-01", "a", 0.40, 0.4),
        ("2000-03-01", "2001-01-01", "a", 0.90, 0.6),
        ("2001-01-01", "2003-06-01", "a", 50.0, 0.4),   # open at 2003-01-01
        ("2003-02-01", "2004-01-01", "a", 0.30, 0.4),
    ])
    out = walkforward(rows, k_current=1.0, cap=None, min_train=1)
    from band_walkforward import fit_power
    a, b = fit_power(np.array([0.05, 0.40, 0.90]), np.array([0.1, 0.4, 0.6]))
    t = out[out["date"] == "2003-02-01"].iloc[0]
    assert abs(t["pred_power"] - a * 0.4 ** b) < 1e-9


def test_fit_l1_recovers_an_exact_linear_model():
    from band_walkforward import fit_l1
    rng = np.random.default_rng(2)
    X = rng.normal(size=(50, 2))
    y = 0.3 + X @ np.array([1.5, -0.7])
    coef = fit_l1(y, X)
    assert np.allclose(coef, [0.3, 1.5, -0.7], atol=1e-6)


def test_walkforward_extra_factor_scales_k_vol_by_z_power():
    from band_walkforward import fit_l1
    rows = _rows([
        ("2000-01-01", "2001-01-01", "a", 0.10, 0.5),
        ("2000-02-01", "2001-01-01", "a", 0.30, 0.5),
        ("2000-03-01", "2001-01-01", "a", 0.20, 0.4),
        ("2003-02-01", "2004-01-01", "a", 0.30, 0.4),
    ])
    rows["z"] = [1.0, 2.0, 1.5, 1.2]
    out = walkforward(rows, k_current=1.0, cap=None, min_train=1, extra="z")
    tr = rows.iloc[:3]
    a, g = fit_l1(np.log(tr["depth"] / tr["vol"]), np.log(tr[["z"]].to_numpy()))
    t = out.iloc[-1]
    assert abs(t["pred_extra"] - 0.4 * np.exp(a) * 1.2 ** g) < 1e-9


def test_walkforward_without_extra_has_no_extra_column():
    rows = _rows([
        ("2000-01-01", "2001-01-01", "a", 0.10, 0.5),
        ("2003-02-01", "2004-01-01", "a", 0.30, 0.4),
    ])
    out = walkforward(rows, k_current=1.0, cap=None, min_train=1)
    assert "pred_extra" not in out.columns
