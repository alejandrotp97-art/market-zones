"""Panel builder for the v2 sandbox. Read-only over production.

Per asset we keep the causal axes from `regime.analyze` (score, vol_p, dd_p,
instab_p, regime label) plus forward returns at the signal horizon and at the
1-month holding horizon. Cross-sectional experiments use an UNBALANCED panel:
at each rebalance date we rank whatever assets have a valid signal (>= MIN_XS),
which keeps 2008 in scope with the assets that existed then."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zones import fetch_daily
from regime import analyze
from regime.dashboard import CURATED

YEARS = 25
AXES = ["score", "vol_p", "dd_p", "instab_p"]
SIGNAL_H = 126          # 6m: horizon at which analogues estimate forward advantage
HOLD_H = 21             # 1m: portfolio holding / IC target horizon
MIN_XS = 8              # minimum assets in the cross-section to rank a date

ERAS = ["2001-2008", "2009-2016", "2017-2020", "2021-actual"]
CRISES = {
    "2008": ("2007-11-01", "2009-06-30"),
    "2020": ("2020-02-01", "2020-12-31"),
    "2022": ("2022-01-01", "2022-12-31"),
}


def era_of(y: int) -> str:
    return "2001-2008" if y < 2009 else "2009-2016" if y < 2017 else "2017-2020" if y < 2021 else "2021-actual"


def _fwd(close: np.ndarray, h: int) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    if n > h:
        out[:n - h] = close[h:] / close[:n - h] - 1.0
    return out


def build_panel():
    """Return {sym: dict of arrays}, plus the sorted monthly rebalance DatetimeIndex."""
    panel = {}
    all_dates = None
    for sym, _ in CURATED:
        try:
            frame, _ = analyze(fetch_daily(sym, years=YEARS))
        except Exception:
            continue
        frame = frame.dropna(subset=["score"]).reset_index(drop=True)
        if len(frame) < SIGNAL_H + HOLD_H + 60:
            continue
        close = frame["close"].to_numpy(float)
        panel[sym] = {
            "dates": pd.DatetimeIndex(frame["date"]),
            "A": frame[AXES].to_numpy(float),
            "regime": frame["regime"].to_numpy(object),
            "close": close,
            "fwd_sig": _fwd(close, SIGNAL_H),
            "fwd_hold": _fwd(close, HOLD_H),
        }
        d = panel[sym]["dates"]
        all_dates = d if all_dates is None else all_dates.union(d)
    # monthly rebalance dates = last available common calendar day per month
    s = pd.Series(all_dates.month, index=all_dates)
    monthly = all_dates[s != s.shift(-1)]
    return panel, monthly
