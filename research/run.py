"""Run the full v2 experimental program (E1-E10) and emit results.json + figures.

Isolated: reads production only for data/features; writes only under research/.
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from research import data as D
from research import criteria as C
from research.predictors import compute_signals, VARIANTS
from research import experiments as E

HERE = os.path.dirname(__file__)
FIGDIR = os.path.join(HERE, "figures")
os.makedirs(FIGDIR, exist_ok=True)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def aggregate(dfv):
    ic = E.rank_ic(dfv)
    pf = E.portfolio(dfv)
    pr = E.probability(dfv)
    cf = E.conformal(dfv)
    return {"ic": ic, "portfolio": pf, "probability": pr, "conformal": cf}


def flat_metrics(agg):
    ic, pf, pr, cf = agg["ic"], agg["portfolio"], agg["probability"], agg["conformal"]
    return {
        "rank_ic": ic.get("rank_ic"), "ic_tstat": ic.get("ic_tstat"),
        "ic_eras_positive": ic.get("ic_eras_positive", 0), "kendall": ic.get("kendall"),
        "ls_sharpe": pf.get("ls_sharpe"), "top_alpha": pf.get("top_alpha"),
        "monotonic": pf.get("monotonic", False), "turnover": pf.get("turnover"),
        "brier_skill": pr.get("brier_skill"), "ece": pr.get("ece"),
        "logloss_skill": pr.get("logloss_skill"),
        "cov_conformal": cf.get("cov_conformal"), "cov_bootstrap": cf.get("cov_bootstrap"),
    }


def score(m):
    clip = lambda x: max(0.0, min(1.0, x))
    ranking = clip((m.get("rank_ic") or 0) / 0.05)
    calibration = clip(1 - (m.get("ece") or 0.2) / 0.10)
    cov = m.get("cov_conformal")
    coverage = clip(1 - abs((cov if cov is not None else 0.7) - 0.95) / 0.10)
    sharpe = clip((m.get("ls_sharpe") or 0) / 0.6)
    stability = clip((m.get("ic_eras_positive") or 0) / 4.0)
    return ranking, calibration, coverage, sharpe, stability


def make_figs(results, dfs):
    st = {"figsize": (6.4, 3.6), "dpi": 110}
    plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.25,
                         "axes.spines.top": False, "axes.spines.right": False})
    # 1 · rank-IC per variant
    fig, ax = plt.subplots(figsize=st["figsize"])
    vs = VARIANTS; ics = [results["variants"][v]["metrics"]["rank_ic"] or 0 for v in vs]
    ax.bar(vs, ics, color=["#4a90d9" if i >= C.RANK_IC_MIN else "#cf5b3a" for i in ics])
    ax.axhline(C.RANK_IC_MIN, ls="--", c="#888", lw=1, label=f"umbral {C.RANK_IC_MIN}")
    ax.axhline(0, c="#444", lw=.8); ax.set_ylabel("Rank IC medio"); ax.legend(); ax.tick_params(axis="x", rotation=20)
    fig.tight_layout(); fig.savefig(os.path.join(FIGDIR, "e1_rank_ic.png")); plt.close(fig)
    # 2 · equity of best-by-sharpe variant
    best = max(vs, key=lambda v: (results["variants"][v]["metrics"]["ls_sharpe"] or -9))
    pf = results["variants"][best]["portfolio"]
    if pf.get("equity"):
        eq = pf["equity"]; x = range(len(eq["ew"]))
        fig, ax = plt.subplots(figsize=st["figsize"])
        ax.plot(x, eq["top"], label="Top 20%", c="#3fae6b")
        ax.plot(x, eq["ew"], label="Equal-weight", c="#888")
        ax.plot(x, eq["bottom"], label="Bottom 20%", c="#cf5b3a")
        ax.set_yscale("log"); ax.set_ylabel("crecimiento de 1 (log)"); ax.set_title(f"{best} · neto de costes")
        ax.legend(); fig.tight_layout(); fig.savefig(os.path.join(FIGDIR, "e2_equity.png")); plt.close(fig)
    # 3 · reliability of best
    pr = results["variants"][best]["probability"]
    if pr.get("reliability"):
        rel = pr["reliability"]
        fig, ax = plt.subplots(figsize=(4.2, 4.2))
        ax.plot([0, 1], [0, 1], ls="--", c="#888")
        ax.plot([r["p"] for r in rel], [r["y"] for r in rel], "o-", c="#4a90d9")
        ax.set_xlabel("prob. predicha"); ax.set_ylabel("frecuencia observada")
        ax.set_title(f"Fiabilidad · {best} · ECE={pr.get('ece',0):.3f}")
        fig.tight_layout(); fig.savefig(os.path.join(FIGDIR, "e3_reliability.png")); plt.close(fig)
    # 4 · coverage bootstrap vs conformal
    fig, ax = plt.subplots(figsize=st["figsize"])
    cb = [results["variants"][v]["metrics"]["cov_bootstrap"] or 0 for v in vs]
    cc = [results["variants"][v]["metrics"]["cov_conformal"] or 0 for v in vs]
    xp = np.arange(len(vs)); w = 0.38
    ax.bar(xp - w/2, cb, w, label="bootstrap", color="#cf5b3a")
    ax.bar(xp + w/2, cc, w, label="conformal", color="#3fae6b")
    ax.axhline(0.95, ls="--", c="#444", lw=1, label="nominal 95%")
    ax.set_xticks(xp); ax.set_xticklabels(vs, rotation=20); ax.set_ylabel("cobertura"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIGDIR, "e4_coverage.png")); plt.close(fig)
    return best


def main():
    t0 = time.time()
    print("building panel…")
    panel, monthly = D.build_panel()
    print(f"  {len(panel)} assets, {len(monthly)} monthly dates")
    print("computing signals (4 variants)…")
    df = compute_signals(panel, monthly)
    print(f"  {len(df)} signal rows, t={time.time()-t0:.0f}s")

    results = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
               "criteria": {k: getattr(C, k) for k in
                            ["RANK_IC_MIN", "IC_TSTAT_MIN", "IC_ERA_POSITIVE_MIN", "LS_SHARPE_MIN",
                             "TOP_ALPHA_MIN", "BRIER_SKILL_MIN", "ECE_MAX", "COVERAGE_MIN", "COVERAGE_MAX", "COST_BPS"]},
               "n_assets": len(panel), "n_months": len(monthly), "variants": {}}
    dfs = {}
    for v in VARIANTS:
        dfv = df[df["variant"] == v]
        dfs[v] = dfv
        agg = aggregate(dfv)
        m = flat_metrics(agg)
        g = C.gate(m)
        r, cal, cov, sh, stab = score(m)
        sc = (C.SCORE_WEIGHTS["ranking"]*r + C.SCORE_WEIGHTS["calibration"]*cal +
              C.SCORE_WEIGHTS["coverage"]*cov + C.SCORE_WEIGHTS["sharpe"]*sh +
              C.SCORE_WEIGHTS["stability"]*stab +
              C.SCORE_WEIGHTS["complexity"]*(1-C.VARIANT_COMPLEXITY[v]) +
              C.SCORE_WEIGHTS["interpretability"]*C.VARIANT_INTERPRET[v])
        results["variants"][v] = {
            "metrics": m, "gates": g, "portfolio": agg["portfolio"], "probability": agg["probability"],
            "conformal": agg["conformal"], "ic_series": agg["ic"].get("series", []),
            "score_parts": {"ranking": r, "calibration": cal, "coverage": cov, "sharpe": sh,
                            "stability": stab}, "score": round(sc, 4)}
        print(f"  {v:14} IC={m['rank_ic']!s:>7.7} tstat={m['ic_tstat']!s:>6.6} "
              f"LS_Sharpe={m['ls_sharpe']!s:>6.6} Brier_skill={m['brier_skill']!s:>7.7} "
              f"ECE={m['ece']!s:>6.6} cov_conf={m['cov_conformal']!s:>5.5} v2={g['v2_worthy']}")

    # ── E7 permutation importance + E8 ablation on euclid_knn ──
    base_ic = results["variants"]["euclid_knn"]["metrics"]["rank_ic"] or 0.0
    perm, abla = {}, {}
    for k, name in enumerate(D.AXES):
        def perm_op(A, k=k):
            A[:, k] = np.random.default_rng(0).permutation(A[:, k]); return A
        def zero_op(A, k=k):
            A[:, k] = 0.0; return A
        dfp = compute_signals(panel, monthly, only=["euclid_knn"], axis_op=perm_op)
        dfa = compute_signals(panel, monthly, only=["euclid_knn"], axis_op=zero_op)
        icp = E.rank_ic(dfp[dfp.variant == "euclid_knn"]).get("rank_ic") or 0.0
        ica = E.rank_ic(dfa[dfa.variant == "euclid_knn"]).get("rank_ic") or 0.0
        perm[name] = round(base_ic - icp, 5)
        abla[name] = round(base_ic - ica, 5)
        print(f"  E7/E8 {name:9} perm_drop={perm[name]:+.4f} ablate_drop={abla[name]:+.4f}")
    results["e7_permutation_importance"] = perm
    results["e8_ablation"] = abla
    results["e8_regime_vs_continuous"] = {
        "discrete_ic": results["variants"]["A_discrete"]["metrics"]["rank_ic"],
        "continuous_ic": results["variants"]["euclid_knn"]["metrics"]["rank_ic"]}

    # ── E9 crisis ──
    crisis = {}
    for name, (s, e) in D.CRISES.items():
        crisis[name] = {}
        for v in ("A_discrete", "euclid_knn"):
            w = E.on_window(dfs[v], s, e)
            crisis[name][v] = {"rank_ic": w["rank_ic"],
                               "ls_sharpe": w["portfolio"].get("ls_sharpe"),
                               "cov_conformal": w["conformal"].get("cov_conformal"),
                               "brier_skill": w["probability"].get("brier_skill"), "n": w["n"]}
    results["e9_crisis"] = crisis

    # ── E10 selection + final decision ──
    passers = [v for v in VARIANTS if results["variants"][v]["gates"]["v2_worthy"]]
    best_score = max(VARIANTS, key=lambda v: results["variants"][v]["score"])
    results["e10"] = {"passers": passers,
                      "ranking_by_score": sorted(VARIANTS, key=lambda v: -results["variants"][v]["score"]),
                      "best_by_score": best_score}
    results["decision"] = {
        "build_v2": len(passers) > 0,
        "rationale": ("Al menos una variante supera los umbrales pre-registrados de ranking Y cartera."
                      if passers else
                      "NINGUNA variante supera los umbrales pre-registrados de ranking y cartera "
                      "out-of-sample. Conclusión válida: NO construir Regime Engine v2.")}

    best_fig = make_figs(results, dfs)
    results["fig_best"] = best_fig

    with open(os.path.join(HERE, "results.json"), "w") as f:
        json.dump(results, f, indent=2, default=_f)
    print(f"\nDECISION build_v2={results['decision']['build_v2']}  passers={passers}")
    print(f"best_by_score={best_score}  t={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
