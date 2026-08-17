"""Daily -> weekly OHLCV resampling (W-SUN, week ending Sunday).

The daily frame from `fetch_daily` is the source of truth; the weekly frame is
derived so the same engine can score a weekly series (see `engine.WEEKLY`). The
aggregation is the one every charting tool uses:

    open = first, high = max, low = min, close = last, volume = sum

Weeks with no contributing daily bar (NaN close) are dropped. Only the OHLCV
columns actually present survive — a futures line whose volume was dropped
upstream simply resamples without it, exactly as the daily path handles it.
"""
from __future__ import annotations

import pandas as pd

_OHLCV = ("open", "high", "low", "close", "volume")
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum"}


def to_weekly(df: pd.DataFrame, rule: str = "W-SUN") -> pd.DataFrame:
    """Resample a daily OHLCV frame (with a 'date' column) to weekly bars.

    `date` may be tz-aware or naive, strings or datetimes; the output always has
    a datetime 'date' column (week-end labels), ascending, index reset.
    """
    cols = ["date", *_OHLCV]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)

    work = df.copy()
    work["date"] = pd.to_datetime(work["date"], utc=True)
    work = work.dropna(subset=["date"]).sort_values("date").set_index("date")

    present = [c for c in _OHLCV if c in work.columns]
    if "close" not in present:
        raise KeyError("to_weekly requires a 'close' column")

    weekly = work[present].resample(rule).agg({c: _AGG[c] for c in present})
    weekly = weekly.dropna(subset=["close"])
    return weekly.reset_index()
