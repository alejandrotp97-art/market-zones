"""E1-E9 metric computations on the long signal DataFrame (per variant slice).

All metrics are out-of-sample by construction: the signal at each date used only
past data, and here we merely compare it against the realised forward outcome.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

from research.data import MIN_XS
from research.criteria import COST_BPS


# ── E1 · Rank IC ─────────────────────────────────────────────────────────
def rank_ic(dfv):
    d = dfv.dropna(subset=["obs_hold"])
    ics, taus, dates = [], [], []
    for dt, g in d.groupby("date"):
        if len(g) < MIN_XS or g["pred_excess"].nunique() < 3:
            continue
        ic = stats.spearmanr(g["pred_excess"], g["obs_hold"]).statistic
        tau = stats.kendalltau(g["pred_excess"], g["obs_hold"]).statistic
        if np.isfinite(ic):
            ics.append(ic); taus.append(tau if np.isfinite(tau) else 0.0); dates.append(dt)
    ics = np.array(ics)
    if len(ics) < 12:
        return {"n": len(ics), "rank_ic": float("nan")}
    tstat = float(np.mean(ics) / (np.std(ics, ddof=1) / np.sqrt(len(ics))))
    # era positivity
    yrs = np.array([d.year for d in dates])
    eras = np.where(yrs < 2009, 0, np.where(yrs < 2017, 1, np.where(yrs < 2021, 2, 3)))
    era_pos = sum(1 for e in range(4) if (eras == e).any() and np.mean(ics[eras == e]) > 0)
    return {"n": int(len(ics)), "rank_ic": float(np.mean(ics)), "ic_median": float(np.median(ics)),
            "kendall": float(np.mean(taus)), "ic_std": float(np.std(ics, ddof=1)),
            "ic_tstat": tstat, "ic_hit": float(np.mean(ics > 0)),
            "ic_eras_positive": int(era_pos),
            "series": [(str(d.date()), float(v)) for d, v in zip(dates, ics)]}


# ── E2 · Portfolio by ranking ────────────────────────────────────────────
def portfolio(dfv, q=0.2):
    d = dfv.dropna(subset=["obs_hold"])
    rt, re, rb, dates, prev_top, prev_bot, turns = [], [], [], [], set(), set(), []
    for dt, g in d.groupby("date"):
        if len(g) < MIN_XS:
            continue
        g = g.sort_values("pred_excess")
        n = len(g); k = max(1, int(round(n * q)))
        bot = g.iloc[:k]; top = g.iloc[-k:]
        rt.append(top["obs_hold"].mean()); rb.append(bot["obs_hold"].mean())
        re.append(g["obs_hold"].mean()); dates.append(dt)
        ttop, tbot = set(top["sym"]), set(bot["sym"])
        turn = (len(ttop - prev_top) / max(1, len(ttop))) if prev_top else 1.0
        turns.append(turn); prev_top, prev_bot = ttop, tbot
    rt, re, rb = np.array(rt), np.array(re), np.array(rb)
    turns = np.array(turns)
    if len(rt) < 12:
        return {"n": len(rt)}
    cost = turns * (COST_BPS / 1e4)
    rt_n, rb_n = rt - cost, rb - cost                     # net of costs
    ls = rt_n - rb_n
    per_yr = 12.0

    def stats_of(r):
        mu, sd = np.mean(r), np.std(r, ddof=1)
        dn = np.std(np.minimum(r, 0), ddof=1)
        cum = np.cumprod(1 + r); peak = np.maximum.accumulate(cum)
        mdd = float(np.min(cum / peak - 1))
        cagr = float(cum[-1] ** (per_yr / len(r)) - 1)
        sharpe = float(mu / sd * np.sqrt(per_yr)) if sd > 1e-9 else float("nan")
        sortino = float(mu / dn * np.sqrt(per_yr)) if dn > 1e-9 else float("nan")
        calmar = float(cagr / abs(mdd)) if mdd < 0 else float("nan")
        return {"cagr": cagr, "sharpe": sharpe, "sortino": sortino, "maxdd": mdd, "calmar": calmar,
                "mean_m": float(mu)}
    S = {"top": stats_of(rt_n), "ew": stats_of(re), "bottom": stats_of(rb_n), "ls": stats_of(ls)}
    S["ls_sharpe"] = S["ls"]["sharpe"]
    S["top_alpha"] = float((np.mean(rt_n) - np.mean(re)) * per_yr)
    S["turnover"] = float(np.mean(turns))
    S["cost_drag_yr"] = float(np.mean(cost) * per_yr)
    S["monotonic"] = bool(np.mean(rt) >= np.mean(re) >= np.mean(rb))
    S["n"] = int(len(rt))
    S["equity"] = {"top": np.cumprod(1 + rt_n).tolist(), "ew": np.cumprod(1 + re).tolist(),
                   "bottom": np.cumprod(1 + rb_n).tolist(),
                   "dates": [str(d.date()) for d in dates]}
    return S


# ── E3 · Probability calibration ─────────────────────────────────────────
def probability(dfv):
    d = dfv.dropna(subset=["obs_sig"])
    if len(d) < 50:
        return {"n": len(d)}
    p = np.clip(d["prob_beat"].to_numpy(float), 1e-6, 1 - 1e-6)
    y = (d["obs_sig"].to_numpy(float) > 0).astype(float)
    base = float(np.mean(y))
    brier = float(np.mean((p - y) ** 2))
    brier_clim = base * (1 - base)
    ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    ll_clim = float(-np.mean(y * np.log(base) + (1 - y) * np.log(1 - base))) if 0 < base < 1 else float("nan")
    bins = np.linspace(0, 1, 11)
    idx = np.clip(np.digitize(p, bins) - 1, 0, 9)
    rel, ece = [], 0.0
    for b in range(10):
        m = idx == b
        if m.any():
            pm, ym, w = float(np.mean(p[m])), float(np.mean(y[m])), float(np.mean(m))
            rel.append({"p": pm, "y": ym, "n": int(m.sum())})
            ece += w * abs(pm - ym)
    return {"n": int(len(d)), "base_rate": base, "brier": brier, "brier_clim": brier_clim,
            "brier_skill": float(1 - brier / brier_clim) if brier_clim > 0 else float("nan"),
            "logloss": ll, "logloss_clim": ll_clim,
            "logloss_skill": float(1 - ll / ll_clim) if ll_clim and ll_clim > 0 else float("nan"),
            "ece": float(ece), "reliability": rel}


# ── E4 · Conformal vs bootstrap coverage ─────────────────────────────────
def conformal(dfv, alpha=0.05):
    d = dfv.dropna(subset=["obs_sig"]).sort_values("date")
    if len(d) < 80:
        return {"n": len(d)}
    dates = d["date"].to_numpy()
    pred = d["pred_point"].to_numpy(float); obs = d["obs_sig"].to_numpy(float)
    lo = d["lo"].to_numpy(float); hi = d["hi"].to_numpy(float)
    resid_buf, cov_c, wid_c, n_c = [], 0, [], 0
    cov_b = np.mean((lo <= obs) & (obs <= hi))
    wid_b = float(np.mean(hi - lo))
    # pooled split-conformal in date order: use residuals strictly before current date
    uniq = np.unique(dates)
    order = {dt: k for k, dt in enumerate(uniq)}
    by_date = {}
    for i in range(len(d)):
        by_date.setdefault(dates[i], []).append(i)
    for dt in uniq:
        if len(resid_buf) >= 50:
            hw = float(np.quantile(np.abs(resid_buf), 1 - alpha))
            for i in by_date[dt]:
                cov_c += int(pred[i] - hw <= obs[i] <= pred[i] + hw)
                wid_c.append(2 * hw); n_c += 1
        for i in by_date[dt]:
            resid_buf.append(obs[i] - pred[i])
    return {"n": int(len(d)),
            "cov_bootstrap": float(cov_b), "width_bootstrap": wid_b,
            "cov_conformal": (float(cov_c / n_c) if n_c else float("nan")),
            "width_conformal": (float(np.mean(wid_c)) if wid_c else float("nan")), "n_conformal": int(n_c)}


# ── E9 · crisis subset (reuses the above on filtered rows) ───────────────
def on_window(dfv, start, end):
    m = (dfv["date"] >= start) & (dfv["date"] <= end)
    sub = dfv[m]
    return {"rank_ic": rank_ic(sub).get("rank_ic"), "portfolio": portfolio(sub),
            "probability": probability(sub), "conformal": conformal(sub), "n": int(m.sum())}
