"""Core validation computations (P5-1 .. P5-8).

Strict-causal walk-forward: the prediction at date t uses ONLY analogues whose
forward window is fully realised before t (j + h <= t). This is stricter than the
shipped calibration sampler (which lets a past analogue's window peek past t), so
some numbers here will look slightly worse than the dashboard's — on purpose.

Metric conventions (documented so the report is auditable):
  err  = obs - pred
  MAE  = mean|err|,  RMSE = sqrt(mean err^2)
  Bias = mean(pred - obs)   ->  >0 means the model OVER-predicts (optimistic)
  Coverage = P(lo <= obs <= hi) for the 95% analogue band  (ideal 95)
  excess_obs = obs - baseline_asof,  excess_pred = pred - baseline_asof
  Strategy (long-only, tactical): take excess_obs when excess_pred>0, else 0.
    Sharpe / HitRate are computed on NON-OVERLAPPING decisions (spacing >= h) so
    autocorrelation from overlapping windows does not inflate them.
"""
from __future__ import annotations

import numpy as np

from regime import analyze
from zones import fetch_daily

VAL_HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252, "24m": 504}
MIN_ANALOG = 20
STEP = 5                      # sample a decision every 5 trading days
YEARS = 25

# ── era boundaries (calendar year -> label) ──
def era_of(year: int) -> str:
    if year < 2009:
        return "2001-2008"
    if year < 2017:
        return "2009-2016"
    if year < 2021:
        return "2017-2020"
    return "2021-actual"


ERAS = ["2001-2008", "2009-2016", "2017-2020", "2021-actual"]


def load_frame(sym: str):
    df = fetch_daily(sym, years=YEARS)
    frame, s = analyze(df)
    return frame, s


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return float("nan")
    ra = np.argsort(np.argsort(a)); rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


# ── P5-1 · strict-causal walk-forward, all horizons ──────────────────────
def walk_forward(frame) -> dict:
    c = frame["close"].to_numpy(float)
    reg = frame["regime"].to_numpy(object)
    yrs = frame["date"].dt.year.to_numpy()
    ts = (frame["date"].astype("int64") // 10**6).to_numpy()
    n = len(c)
    out = {}
    for hname, h in VAL_HORIZONS.items():
        fwd = np.full(n, np.nan)
        if n > h:
            fwd[:n - h] = c[h:] / c[:n - h] - 1.0
        recs = []
        pools_reg: dict = {}
        pool_all: list = []
        last = 0
        for t in range(n):
            # admit analogues whose full window ended at or before t (j + h <= t)
            while last <= t - h and last < n:
                j = last
                if np.isfinite(fwd[j]) and isinstance(reg[j], str):
                    pools_reg.setdefault(reg[j], []).append(fwd[j])
                    pool_all.append(fwd[j])
                last += 1
            if t % STEP or t > n - 1 - h:
                continue
            rt = reg[t]
            pr = pools_reg.get(rt)
            if not isinstance(rt, str) or pr is None or len(pr) < MIN_ANALOG or not pool_all:
                continue
            arr = np.asarray(pr)
            base = float(np.median(pool_all))
            recs.append({
                "t": int(ts[t]), "yr": int(yrs[t]), "era": era_of(int(yrs[t])), "regime": rt,
                "pred": float(np.median(arr)),
                "lo": float(np.percentile(arr, 2.5)), "hi": float(np.percentile(arr, 97.5)),
                "obs": float(fwd[t]), "base": base, "meanhist": float(np.mean(pool_all)),
                "mom": (float(c[t] / c[t - h] - 1.0) if t - h >= 0 else float("nan")),
                "pit": float(np.mean(arr <= fwd[t])), "nanalog": len(arr),
            })
        out[hname] = recs
    return out


# ── metric core on a list of records ─────────────────────────────────────
def _nonoverlap(recs, h):
    """Subsample so decisions are >= h trading days apart (records are STEP apart)."""
    gap = max(1, int(np.ceil(h / STEP)))
    return recs[::gap]


def metrics(recs, h) -> dict:
    if len(recs) < 10:
        return {"n": len(recs)}
    pred = np.array([r["pred"] for r in recs]); obs = np.array([r["obs"] for r in recs])
    lo = np.array([r["lo"] for r in recs]); hi = np.array([r["hi"] for r in recs])
    base = np.array([r["base"] for r in recs])
    err = obs - pred
    exc_obs = obs - base; exc_pred = pred - base
    cov = float(np.mean((lo <= obs) & (obs <= hi)) * 100)
    # strategy on non-overlapping decisions
    no = _nonoverlap(recs, h)
    p2 = np.array([r["pred"] for r in no]); o2 = np.array([r["obs"] for r in no]); b2 = np.array([r["base"] for r in no])
    sig = (p2 - b2) > 0
    strat = np.where(sig, o2 - b2, 0.0)
    ann = np.sqrt(252.0 / h)
    sharpe = float(np.mean(strat) / np.std(strat) * ann) if np.std(strat) > 1e-12 else float("nan")
    took = (o2 - b2)[sig]
    hit = float(np.mean(took > 0) * 100) if took.size else float("nan")
    return {
        "n": len(recs), "n_noov": len(no),
        "mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2))),
        "bias": float(np.mean(pred - obs)), "coverage": cov,
        "sharpe": sharpe, "hit": hit,
        "corr": _pearson(pred, obs), "scorr_excess": _spearman(exc_pred, exc_obs),
        "mean_excess": float(np.mean(exc_obs)),
    }


# ── P5-2 · probabilistic calibration ─────────────────────────────────────
def calibration(recs) -> dict:
    if len(recs) < 30:
        return {"n": len(recs)}
    pred = np.array([r["pred"] for r in recs]); obs = np.array([r["obs"] for r in recs])
    pit = np.array([r["pit"] for r in recs])
    slope, intercept = np.polyfit(pred, obs, 1)
    r2 = _pearson(pred, obs) ** 2
    # reliability by prediction deciles
    order = np.argsort(pred); k = 10
    bins = np.array_split(order, k)
    rel = [{"pred": float(np.mean(pred[b])), "obs": float(np.mean(obs[b])), "n": len(b)}
           for b in bins if len(b)]
    # distributional calibration via PIT: empirical coverage vs nominal
    levels = [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]
    ece = float(np.mean([abs(np.mean(pit <= q) - q) for q in levels]))
    pit_curve = [{"nominal": q, "empirical": float(np.mean(pit <= q))} for q in levels]
    return {"n": len(recs), "slope": float(slope), "intercept": float(intercept),
            "r2": float(r2), "ece": ece, "reliability": rel, "pit_curve": pit_curve}


# ── P5-5 · circular block bootstrap CIs of the metrics ───────────────────
def bootstrap_ci(recs, h, B=1000, seed=0) -> dict:
    no = _nonoverlap(recs, h)
    if len(no) < 12:
        return {"n": len(no)}
    rng = np.random.default_rng(seed)
    o = np.array([r["obs"] for r in no]); p = np.array([r["pred"] for r in no]); b = np.array([r["base"] for r in no])
    n = len(no); block = max(1, int(np.ceil(np.sqrt(n))))
    nb = int(np.ceil(n / block)); ann = np.sqrt(252.0 / h)
    maes, shs, hits, exs = [], [], [], []
    for _ in range(B):
        starts = rng.integers(0, n, nb)
        idx = np.concatenate([(np.arange(s, s + block) % n) for s in starts])[:n]
        oi, pi, bi = o[idx], p[idx], b[idx]
        maes.append(np.mean(np.abs(oi - pi)))
        sig = (pi - bi) > 0; strat = np.where(sig, oi - bi, 0.0)
        shs.append(np.mean(strat) / np.std(strat) * ann if np.std(strat) > 1e-12 else np.nan)
        took = (oi - bi)[sig]; hits.append(np.mean(took > 0) * 100 if took.size else np.nan)
        exs.append(np.mean(oi - bi))
    ci = lambda a: [float(np.nanpercentile(a, 2.5)), float(np.nanpercentile(a, 97.5))]
    return {"n": len(no), "mae": ci(maes), "sharpe": ci(shs), "hit": ci(hits), "excess": ci(exs)}


# ── P5-6 · current-day evidence p-value (for FDR across assets×horizons) ──
def current_pvalue(frame, h, B=1500, seed=1):
    """Block-bootstrap two-sided p-value that the SHIPPED current excess != 0.
    Uses the same analogue definition the dashboard ships (same regime, past days)."""
    c = frame["close"].to_numpy(float); reg = frame["regime"].to_numpy(object); n = len(c)
    q = n - 1
    fwd = np.full(n, np.nan)
    if n > h:
        fwd[:n - h] = c[h:] / c[:n - h] - 1.0
    rq = reg[q]
    if not isinstance(rq, str):
        return None
    idx = [j for j in range(q) if reg[j] == rq and np.isfinite(fwd[j])]
    allr = fwd[np.isfinite(fwd)]
    if len(idx) < 10 or allr.size < 10:
        return None
    r = fwd[idx]; base = float(np.median(allr))
    med_exc = float(np.median(r) - base)
    rng = np.random.default_rng(seed)
    nn = len(r); block = max(1, min(h, nn)); nb = int(np.ceil(nn / block))
    meds = np.empty(B)
    for bi in range(B):
        starts = rng.integers(0, nn, nb)
        ii = np.concatenate([(np.arange(s, s + block) % nn) for s in starts])[:nn]
        meds[bi] = np.median(r[ii]) - base
    frac_pos = float(np.mean(meds > 0))
    p = 2.0 * min(frac_pos, 1.0 - frac_pos)
    p = max(1.0 / B, min(1.0, p))
    return {"excess": med_exc, "p": p, "n": nn}


def benjamini_hochberg(pvals, q=0.05):
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i]["p"])
    thresh = 0
    for rank, i in enumerate(order, 1):
        if pvals[i]["p"] <= q * rank / m:
            thresh = rank
    survivors = {order[i] for i in range(thresh)}
    for rank, i in enumerate(order, 1):
        pvals[i]["bh_rank"] = rank
        pvals[i]["survives"] = i in survivors
    return {"m": m, "q": q, "n_raw_sig": sum(1 for x in pvals if x["p"] < 0.05),
            "expected_fp": m * 0.05, "n_survive": len(survivors)}


# ── P5-7 · Opportunity sensitivity / elasticities ────────────────────────
def _opp_from_rows(rows):
    import math
    cs = []
    for r in rows:
        if r.get("baseline") is None or r.get("excess") is None:
            continue
        ch = (r["ci_hi"] - r["ci_lo"]) / 2.0
        snr = r["excess"] / ch if ch > 1e-9 else 0.0
        cs.append(math.tanh(0.6 * snr) * math.sqrt(min(1.0, r["n_eff"] / 30.0)))
    if not cs:
        return None
    return max(0.0, min(100.0, 50.0 + 50.0 * sum(cs) / len(cs)))


def sensitivity(payload) -> dict:
    """Elasticity of Opportunity to +/-20% shocks in n_eff, IC width, excess.
    Operates on the shipped scenario rows (derived only)."""
    base_rows = []
    for _name, r in payload["scenarios"].items():
        if r.get("baseline") is None or r.get("excess") is None:
            continue
        base_rows.append({"excess": r["excess"], "n_eff": r["n_eff"],
                          "ci_lo": r["ci_lo"], "ci_hi": r["ci_hi"], "baseline": r["baseline"]})
    o0 = _opp_from_rows(base_rows)
    if o0 is None:
        return {}
    def shock(var, f):
        rows = []
        for r in base_rows:
            q = dict(r)
            if var == "n_eff":
                q["n_eff"] = r["n_eff"] * f
            elif var == "excess":
                q["excess"] = r["excess"] * f
                half = (r["ci_hi"] - r["ci_lo"]) / 2.0
                mid = r["baseline"] + q["excess"]
                q["ci_lo"] = mid - half; q["ci_hi"] = mid + half
            elif var == "ic":
                half = (r["ci_hi"] - r["ci_lo"]) / 2.0 * f
                mid = r["baseline"] + r["excess"]
                q["ci_lo"] = mid - half; q["ci_hi"] = mid + half
            rows.append(q)
        return _opp_from_rows(rows)
    out = {"base_opp": o0}
    for var in ("n_eff", "excess", "ic"):
        up = shock(var, 1.2); dn = shock(var, 0.8)
        # elasticity ~ (%dOpp)/(%dInput) averaged over +/-20%
        e_up = ((up - o0) / o0) / 0.2 if o0 else float("nan")
        e_dn = ((o0 - dn) / o0) / 0.2 if o0 else float("nan")
        out[var] = {"up": up, "dn": dn, "elasticity": (e_up + e_dn) / 2.0}
    return out


# ── P5-8 · naive-model comparison ────────────────────────────────────────
def naive_compare(recs, h) -> dict:
    if len(recs) < 10:
        return {"n": len(recs)}
    obs = np.array([r["obs"] for r in recs]); pred = np.array([r["pred"] for r in recs])
    base = np.array([r["base"] for r in recs]); mh = np.array([r["meanhist"] for r in recs])
    mom = np.array([r["mom"] for r in recs])
    mask = np.isfinite(mom)
    mae = lambda f: float(np.mean(np.abs(obs - f)))
    # strategy sharpe (non-overlap)
    no = _nonoverlap(recs, h); ann = np.sqrt(252.0 / h)
    o2 = np.array([r["obs"] for r in no]); b2 = np.array([r["base"] for r in no])
    p2 = np.array([r["pred"] for r in no]); m2 = np.array([r["mom"] for r in no])
    def sh(sig, ret):
        s = np.where(sig, ret, 0.0)
        return float(np.mean(s) / np.std(s) * ann) if np.std(s) > 1e-12 else float("nan")
    def sh_all(ret):
        return float(np.mean(ret) / np.std(ret) * ann) if np.std(ret) > 1e-12 else float("nan")
    return {
        "n": len(recs),
        "mae_model": mae(pred), "mae_zero": mae(base), "mae_meanhist": mae(mh),
        "mae_momentum": float(np.mean(np.abs((obs - mom)[mask]))) if mask.any() else float("nan"),
        "sharpe_model": sh((p2 - b2) > 0, o2 - b2),
        "sharpe_buyhold": sh_all(o2),
        "sharpe_momentum": sh(np.nan_to_num(m2) > 0, o2),
        "skill_vs_zero": 1.0 - mae(pred) / mae(base) if mae(base) > 0 else float("nan"),
    }
