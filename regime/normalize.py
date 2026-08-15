"""No-lookahead normalization — the core correctness fix over the legacy engine.

`expanding_percentile` maps each observation to its percentile within the history
available *up to and including that day only*. The value at time t never depends
on any data after t, so the whole pipeline is causal by construction. Rank-based,
so it is robust to outliers without winsorization.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right, insort

import numpy as np


def expanding_percentile(x, min_periods: int = 504):
    """Percentile rank (0-100) of each point within the expanding window
    [0 .. t]. NaN until `min_periods` finite observations exist. O(n·log n)
    lookups with O(n) insertions — trivial for daily series.

    min_periods default 504 ≈ 2 trading years of burn-in (per FASE 4 spec).
    """
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    seen: list[float] = []
    for i, v in enumerate(x):
        if not np.isfinite(v):
            continue                      # gaps do not enter the reference set
        insort(seen, v)
        n = len(seen)
        if n >= min_periods:
            lo = bisect_left(seen, v)
            hi = bisect_right(seen, v)
            midrank = (lo + hi) / 2.0     # average rank for ties
            out[i] = 100.0 * midrank / n
    return out
