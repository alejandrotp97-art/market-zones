"""Pure normalization primitives that map raw indicators onto a 0-100 scale.

No I/O, no data source: these are the reusable building blocks the scoring
model composes. NaN in -> NaN out (warm-up periods stay undefined).

Two flavours of every primitive:

  IN-SAMPLE   (`z_to_100`, `pct_rank`) — normalize against the WHOLE series.
              The value at t depends on data after t, so the historical curve
              is revisionist: it is not what the index would have said that day.
  EXPANDING   (`expanding_z_to_100`, `expanding_pct_rank`) — normalize against
              [0..t] only. Causal by construction.

The expanding variants are calibrated to agree EXACTLY with the in-sample ones
on the last row (where [0..t] is the whole series), so switching an indicator
to the causal path never moves today's reading — it only stops rewriting the
past. `expanding_pct_rank` therefore reproduces pandas' average-rank convention
rather than the midrank used by `regime.normalize.expanding_percentile`.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right, insort

import numpy as np
import pandas as pd

Z_MIN_PERIODS = 252     # ~1 trading year of the indicator before it is scored


def _norm_cdf(z: np.ndarray) -> np.ndarray:
    """Standard-normal CDF, element-wise, NaN-preserving.

    Kept dependency-free (math.erf instead of scipy) so the core runs on any
    interpreter that has numpy+pandas. Daily series are small, so the Python
    loop is irrelevant for performance.
    """
    z = np.asarray(z, dtype=float)
    out = np.empty_like(z)
    flat, o = z.ravel(), out.ravel()
    for i, v in enumerate(flat):
        o[i] = 0.5 * (1.0 + math.erf(v / math.sqrt(2.0))) if np.isfinite(v) else np.nan
    return out


def z_to_100(x: pd.Series) -> pd.Series:
    """Z-score a series over its own history, then squash to 0-100 via the
    normal CDF. Mean maps to 50; +/-1 sigma to ~84/16. Bounded and monotonic.

    This is the "z-score -> 0-100" step used by Stretch and TrendDev. The
    z-score is computed in-sample (descriptive index, not a forecast).
    """
    x = pd.Series(x, dtype=float)
    mean = np.nanmean(x.to_numpy())
    std = np.nanstd(x.to_numpy())
    if not np.isfinite(std) or std == 0.0:
        # Degenerate history: everything is "neutral", warm-up stays NaN.
        return pd.Series(np.where(np.isfinite(x), 50.0, np.nan), index=x.index)
    z = (x - mean) / std
    return pd.Series(100.0 * _norm_cdf(z.to_numpy()), index=x.index)


def pct_rank(x: pd.Series) -> pd.Series:
    """Percentile rank in 0-100: where does today sit within the asset's own
    historical distribution of this quantity? Used by Drawdown ("deep is
    relative to the asset itself"). NaN-preserving.
    """
    x = pd.Series(x, dtype=float)
    return x.rank(pct=True) * 100.0


def expanding_z_to_100(x: pd.Series, min_periods: int = Z_MIN_PERIODS) -> pd.Series:
    """Causal `z_to_100`: at each t the mean/std come from the finite values in
    [0..t] only, so the value at t never depends on data after t.

    NaN until `min_periods` finite observations exist. A degenerate window
    (zero variance) maps to 50, matching the in-sample branch.
    """
    x = pd.Series(x, dtype=float)
    v = x.to_numpy(dtype=float)
    fin = np.isfinite(v)
    out = np.full(len(v), np.nan)
    if not fin.any():
        return pd.Series(out, index=x.index)

    # Accumulate the sums of squares around a CONSTANT shift (the first finite
    # observation) instead of around 0. Variance is invariant to the shift, but
    # E[x^2]-E[x]^2 on unshifted data cancels catastrophically when the values
    # are large relative to their spread: a series centred on 1e9 loses ~4.5
    # points of score. The indicators fed in today (Mayer ~1, realized vol ~0.2,
    # trend residuals ~0) are all safe, so this costs nothing now — it stops the
    # next component that z-scores a raw price level from being quietly wrong.
    c = float(v[np.argmax(fin)])
    d = np.where(fin, v - c, 0.0)
    cnt = np.cumsum(fin)
    s1 = np.cumsum(d)
    s2 = np.cumsum(d * d)

    ok = fin & (cnt >= max(1, min_periods))
    if ok.any():
        n = cnt[ok].astype(float)
        m = s1[ok] / n                                   # mean of the shifted data
        std = np.sqrt(np.maximum(s2[ok] / n - m * m, 0.0))
        safe = std > 0.0
        z = np.where(safe, (d[ok] - m) / np.where(safe, std, 1.0), 0.0)
        out[ok] = 100.0 * _norm_cdf(z)
    return pd.Series(out, index=x.index)


def expanding_pct_rank(x: pd.Series, min_periods: int = 1) -> pd.Series:
    """Causal `pct_rank`: percentile of each value within [0..t] only.

    Uses pandas' average-rank convention for ties — ranks lo+1..hi averaged,
    divided by the count seen so far — so the last row equals `pct_rank`
    exactly. NaN gaps never enter the reference set.
    """
    x = pd.Series(x, dtype=float)
    v = x.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    seen: list[float] = []
    for i, val in enumerate(v):
        if not np.isfinite(val):
            continue
        insort(seen, val)
        n = len(seen)
        if n >= max(1, min_periods):
            lo = bisect_left(seen, val)
            hi = bisect_right(seen, val)
            out[i] = 100.0 * ((lo + 1 + hi) / 2.0) / n
    return pd.Series(out, index=x.index)
