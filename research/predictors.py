"""Causal signal variants for the v2 sandbox.

At each rebalance date t (integer position in an asset's arrays) we form an
analogue set using ONLY past days whose signal-horizon window fully closed before
t (j + SIGNAL_H <= t). From the analogue forward returns we derive:
  pred_point   weighted median forward return
  pred_excess  pred_point - own-history baseline   (the cross-sectional signal)
  prob_beat    weighted P(forward > baseline)       (the probability output)
  lo, hi       weighted 2.5/97.5 predictive band     (empirical interval)

Variants:
  A_discrete    same hard regime label (the current engine's conditioning)
  euclid_knn    k-NN in the continuous axis space, Euclidean
  C_kernel      Gaussian-kernel weighting in axis space (soft / fuzzy)
  B_mahalanobis k-NN with causal Mahalanobis distance (decorrelated axes)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.data import SIGNAL_H, era_of

VARIANTS = ["A_discrete", "euclid_knn", "C_kernel", "B_mahalanobis"]
K = 150
MIN_DISCRETE = 20
MIN_CONT = 40


def _wquantile(x, w, q):
    o = np.argsort(x); x = x[o]; w = w[o]
    cw = np.cumsum(w) - 0.5 * w
    cw /= w.sum()
    return float(np.interp(q, cw, x))


def _wmedian(x, w):
    return _wquantile(x, w, 0.5)


def _stats(fret, w, base):
    """Weighted point/excess/prob/band from analogue forward returns `fret`."""
    if w is None:
        w = np.ones(len(fret))
    pt = _wmedian(fret, w)
    return {
        "pred_point": pt, "pred_excess": pt - base,
        "prob_beat": float(np.sum(w * (fret > base)) / np.sum(w)),
        "lo": _wquantile(fret, w, 0.025), "hi": _wquantile(fret, w, 0.975),
    }


def compute_signals(panel, monthly, only=None, axis_op=None):
    """Return a long DataFrame: one row per (date, sym, variant) with signal + realised.

    only: optional list of variant names to compute (for cheap E7/E8 recomputes).
    axis_op: optional callable(A)->A applied per asset before distances (permute/zero
             an axis) — used by permutation importance and ablation. Discrete variant
             is unaffected by axis_op (it conditions on the regime label, not axes)."""
    want = set(only) if only else set(VARIANTS)
    rows = []
    for sym, P in panel.items():
        dates = P["dates"]; A = P["A"]; reg = P["regime"]
        if axis_op is not None:
            A = axis_op(A.copy())
        fsig = P["fwd_sig"]; fhold = P["fwd_hold"]
        pos = pd.Series(np.arange(len(dates)), index=dates)
        # rebalance positions for this asset = monthly dates it actually has
        rb = [(d, int(pos[d])) for d in monthly if d in pos.index]
        for d, t in rb:
            cutoff = t - SIGNAL_H                      # last admissible analogue start
            if cutoff < 60:
                continue
            valid = np.where(np.isfinite(fsig[:cutoff + 1]))[0]
            if valid.size < MIN_DISCRETE:
                continue
            base = float(np.median(fsig[valid]))
            at = A[t]
            if not np.all(np.isfinite(at)):
                continue
            obs_sig = fsig[t]; obs_hold = fhold[t]
            common = {"date": d, "sym": sym, "regime": reg[t], "era": era_of(d.year),
                      "obs_sig": (float(obs_sig) if np.isfinite(obs_sig) else np.nan),
                      "obs_hold": (float(obs_hold) if np.isfinite(obs_hold) else np.nan),
                      "baseline": base}
            Av = A[valid]
            # ---- A_discrete ----
            rt = reg[t]
            if "A_discrete" in want and isinstance(rt, str):
                dmask = np.array([reg[j] == rt for j in valid])
                if dmask.sum() >= MIN_DISCRETE:
                    fr = fsig[valid][dmask]
                    rows.append({**common, "variant": "A_discrete", **_stats(fr, None, base)})
            # ---- continuous distances ----
            if valid.size >= MIN_CONT and want & {"euclid_knn", "C_kernel", "B_mahalanobis"}:
                diff = Av - at
                de = np.sqrt((diff ** 2).sum(axis=1))
                k = min(K, valid.size)
                if "euclid_knn" in want:
                    nn = np.argpartition(de, k - 1)[:k]
                    rows.append({**common, "variant": "euclid_knn", **_stats(fsig[valid][nn], None, base)})
                if "C_kernel" in want:
                    sig = np.median(de) or 1.0
                    w = np.exp(-(de ** 2) / (2 * sig ** 2))
                    if w.sum() > 1e-9:
                        rows.append({**common, "variant": "C_kernel", **_stats(fsig[valid], w, base)})
                if "B_mahalanobis" in want:
                    try:
                        cov = np.cov(Av.T)
                        ic = np.linalg.pinv(cov + 1e-6 * np.eye(cov.shape[0]))
                        dm = np.sqrt(np.einsum("ij,jk,ik->i", diff, ic, diff))
                        nn = np.argpartition(dm, k - 1)[:k]
                        rows.append({**common, "variant": "B_mahalanobis", **_stats(fsig[valid][nn], None, base)})
                    except Exception:
                        pass
    df = pd.DataFrame(rows)
    return df
