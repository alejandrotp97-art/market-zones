"""Conditional historical analytics — "desde una situación como la de hoy, ¿qué
pasó después?".

Two ways to define "situación como hoy": by regime label, or k-NN in the
normalized axis space. For the analogue set we report the forward-return
distribution, forward max-drawdown and forward vol at several horizons, each with
a bootstrap confidence interval.

Statistical honesty (per the dossier):
  - Forward windows OVERLAP -> returns are autocorrelated. CIs use a circular
    MOVING-BLOCK bootstrap (block ≈ horizon), never the iid formula.
  - A horizon reports either FULL metrics (median + bootstrap CI) or nothing but
    its count: a median without the interval that qualifies it is not published.
    `MIN_BOOT_N` is the single threshold both sides agree on.
  - Analogues are strictly PAST days (`i < q`, enforced in `_analog_indices`)
    AND, per horizon, days whose forward window had already closed at q
    (`i + h <= q`, enforced in `conditional_stats`). Both halves are required:
    the first stops the analogue itself being in the future, the second stops
    its OUTCOME being in the future.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

HORIZONS = {"1s": 5, "1m": 21, "3m": 63, "6m": 126, "12m": 252}
AXES = ["score", "vol_p", "dd_p", "instab_p"]
LOW_CONFIDENCE_N = 30
# Minimum analogues for a horizon to report FULL metrics. This is the block
# bootstrap's own floor: below it `_block_boot_ci` returns (nan, nan), so a
# lower threshold here would emit a row with a median but no CI — a shape every
# consumer then has to special-case. One constant, one contract.
MIN_BOOT_N = 10


def _forward(close: pd.Series):
    c = np.asarray(close, dtype=float)
    n = len(c)
    lr = np.diff(np.log(c), prepend=np.nan)
    fret, fdd, fvol = {}, {}, {}
    for h in set(HORIZONS.values()):
        fr = np.full(n, np.nan); fd = np.full(n, np.nan); fv = np.full(n, np.nan)
        if n > h:
            fr[:n-h] = c[h:] / c[:n-h] - 1.0
            sw = sliding_window_view(c, h)                 # sw[i] = c[i:i+h]
            fd[:n-h] = sw[1:n-h+1].min(axis=1) / c[:n-h] - 1.0
            swr = sliding_window_view(lr, h)               # returns windows
            fv[:n-h] = np.nanstd(swr[1:n-h+1], axis=1) * np.sqrt(252.0)
        fret[h], fdd[h], fvol[h] = fr, fd, fv
    return fret, fdd, fvol


_BOOT_CELLS = 2_000_000     # index cells held at once; caps the transient spike


def _block_boot_ci(x: np.ndarray, block: int, B: int = 800, seed: int = 0):
    """Circular moving-block bootstrap CI of the median.

    Vectorized over replicates: the naive loop built one `np.arange` per block
    per replicate (~420k tiny allocations for a single panel, 377 ms of the
    1.3 s build). Replicates are drawn in CHUNKS so the index matrix stays
    bounded regardless of sample size.

    The draw order is unchanged — `rng.integers(size=(k, nb))` fills row-major,
    so row b receives exactly the values the b-th individual call consumed.
    Same seed, same numbers, bit for bit.
    """
    x = x[np.isfinite(x)]
    n = len(x)
    if n < MIN_BOOT_N:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    block = max(1, min(block, n))
    nb = int(np.ceil(n / block))
    offs = np.arange(block)
    chunk = max(1, min(B, _BOOT_CELLS // max(1, nb * block)))
    meds = np.empty(B)
    done = 0
    while done < B:
        k = min(chunk, B - done)
        starts = rng.integers(0, n, size=(k, nb))
        idx = (starts[:, :, None] + offs) % n              # (k, nb, block)
        meds[done:done + k] = np.median(x[idx.reshape(k, -1)[:, :n]], axis=1)
        done += k
    return (float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5)))


def _analog_indices(frame: pd.DataFrame, q: int, method: str, k: int) -> np.ndarray:
    """Indices of past days resembling `q`. ALWAYS integer dtype — an empty
    `np.array([])` defaults to float64 and blows up as a fancy index, which is
    reachable whenever today's regime has never occurred before.
    """
    ax = frame[AXES].to_numpy()
    valid_past = np.where(np.all(np.isfinite(ax[:q]), axis=1))[0]
    if method == "regime":
        rq = frame["regime"].iloc[q]
        return np.array([i for i in valid_past if frame["regime"].iloc[i] == rq],
                        dtype=int)
    d = np.sqrt(((ax[valid_past] - ax[q]) ** 2).sum(axis=1))   # Euclidean in 0-100 axes
    return valid_past[np.argsort(d)[:k]].astype(int)


def conditional_stats(frame: pd.DataFrame, query: int = -1, method: str = "regime", k: int = 250) -> dict:
    """Forward distribution from historical analogues of `query` (default = last row).

    Per horizon, an analogue is admissible only if its forward window had fully
    closed by `query` (i + h <= q) — otherwise the answer to "what happened
    after a day like today?" would be built from prices that, on day `q`, had
    not happened yet. At q = last row the filter is a no-op (those rows carry
    NaN forward returns anyway), so it costs the live panel nothing and makes
    the guarantee real for historical queries too.
    """
    n = len(frame)
    q = query if query >= 0 else n - 1
    fret, fdd, fvol = _forward(frame["close"])
    idx = _analog_indices(frame, q, method, k)

    out = {"method": method, "regime": frame["regime"].iloc[q],
           "n_analogs": len(idx), "horizons": {}}
    for name, h in HORIZONS.items():
        adm = idx[idx + h <= q] if len(idx) else idx
        r = fret[h][adm]; r = r[np.isfinite(r)]
        dd = fdd[h][adm]; dd = dd[np.isfinite(dd)]
        vol = fvol[h][adm]; vol = vol[np.isfinite(vol)]
        if len(r) < MIN_BOOT_N:
            # Not enough analogues for a bootstrap CI. Report the count only —
            # never a median without the interval that qualifies it.
            out["horizons"][name] = {"n": len(r)}
            continue
        lo, hi = _block_boot_ci(r, block=h)
        out["horizons"][name] = {
            "n": len(r),
            "median": float(np.median(r)), "mean": float(np.mean(r)),
            "p10": float(np.percentile(r, 10)), "p90": float(np.percentile(r, 90)),
            "ci_lo": lo, "ci_hi": hi,
            "median_dd": float(np.median(dd)) if len(dd) else np.nan,
            "median_vol": float(np.median(vol)) if len(vol) else np.nan,
        }
    out["low_confidence"] = out["n_analogs"] < LOW_CONFIDENCE_N
    return out
