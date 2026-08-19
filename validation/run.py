"""Run the full validation battery over the curated universe and dump results.json.

Read-only over the engine. One Yahoo fetch per asset (the frame is reused for the
walk-forward, the current p-value and the shipped-scenario sensitivity)."""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from regime.analogs import HORIZONS, _forward, conditional_stats
from regime.dashboard import CURATED
from validation import core

OUT = os.path.join(os.path.dirname(__file__), "results.json")
PRIMARY = ["3m", "6m", "12m"]


def shipped_scenarios(frame):
    """Reconstruct the shipped scenario rows (excess/n_eff/ci/baseline) from the same
    frame, mirroring regime.dashboard._build — read-only, for the sensitivity study."""
    sc = conditional_stats(frame, method="regime")
    fret, _, _ = _forward(frame["close"])
    rows = {}
    for name, hd in HORIZONS.items():
        h = sc["horizons"].get(name, {})
        if "median" not in h:
            continue
        col = fret[hd]
        base = float(np.nanmedian(col)) if np.isfinite(col).any() else None
        if base is None:
            continue
        rows[name] = {"excess": h["median"] - base, "n_eff": round(h["n"] / hd, 1),
                      "ci_lo": h["ci_lo"], "ci_hi": h["ci_hi"], "baseline": base}
    return {"scenarios": rows}


def causality_check(sym="SPY"):
    """Hide the last 400 rows and confirm regime labels on the retained past are
    byte-identical — substantiates the causal claim empirically."""
    from regime import analyze
    from zones import fetch_daily
    df = fetch_daily(sym, years=core.YEARS)
    full, _ = analyze(df)
    cut = len(full) - 400
    trunc, _ = analyze(df.iloc[:cut].copy())
    a = full["regime"].to_numpy()[:cut]
    b = trunc["regime"].to_numpy()
    m = min(len(a), len(b)); a = a[:m]; b = b[:m]
    # compare only labelled rows (warm-up regime is NaN and NaN!=NaN would false-fail)
    strmask = np.array([isinstance(x, str) and isinstance(y, str) for x, y in zip(a, b)])
    labeled = int(strmask.sum())
    differ = int(np.sum(strmask & (a != b)))
    sa = full["score"].to_numpy()[:m]; sb = trunc["score"].to_numpy()[:m]
    fin = np.isfinite(sa) & np.isfinite(sb)
    smd = float(np.max(np.abs(sa[fin] - sb[fin]))) if fin.any() else float("nan")
    return {"asset": sym, "identical": labeled - differ, "total": labeled, "differ": differ,
            "score_max_diff": smd, "passes": (differ == 0 and smd < 1e-9)}


def main():
    t0 = time.time()
    assets = [s for s, _ in CURATED]
    pooled = {h: [] for h in core.VAL_HORIZONS}
    fdr_items = []
    sens_rows = []
    ok = []
    for sym in assets:
        try:
            frame, s = core.load_frame(sym)
        except Exception as e:
            print(f"  skip {sym}: {e}"); continue
        wf = core.walk_forward(frame)
        for h in core.VAL_HORIZONS:
            pooled[h].extend(wf[h])
        for hname, hd in core.VAL_HORIZONS.items():
            pv = core.current_pvalue(frame, hd)
            if pv:
                fdr_items.append({"asset": sym, "horizon": hname, **pv})
        try:
            sens = core.sensitivity(shipped_scenarios(frame))
            if sens:
                sens_rows.append({"asset": sym, **sens})
        except Exception as e:
            print(f"  sens {sym}: {e}")
        ok.append(sym)
        print(f"  {sym:9} pts6m={len(wf['6m'])}  t={time.time()-t0:.1f}s")

    # ── per-horizon aggregates ──
    per_h = {}
    for hname, hd in core.VAL_HORIZONS.items():
        recs = pooled[hname]
        per_h[hname] = {"metrics": core.metrics(recs, hd), "calibration": core.calibration(recs),
                        "bootstrap": core.bootstrap_ci(recs, hd), "naive": core.naive_compare(recs, hd)}

    # ── P5-3 era split (primary horizons) ──
    era = {}
    for hname in PRIMARY:
        hd = core.VAL_HORIZONS[hname]
        era[hname] = {}
        for e in core.ERAS:
            r = [x for x in pooled[hname] if x["era"] == e]
            era[hname][e] = core.metrics(r, hd)

    # ── P5-4 regime split (primary horizons) ──
    reg = {}
    for hname in PRIMARY:
        hd = core.VAL_HORIZONS[hname]
        reg[hname] = {}
        regimes = sorted({x["regime"] for x in pooled[hname]})
        for rg in regimes:
            r = [x for x in pooled[hname] if x["regime"] == rg]
            m = core.metrics(r, hd)
            m["n_eff"] = round(len(r) * core.STEP / hd, 1)
            reg[hname][rg] = m

    # ── P5-6 FDR ──
    fdr_summary = core.benjamini_hochberg(fdr_items) if fdr_items else {}

    # ── P5-7 sensitivity aggregate ──
    def mean_elast(var):
        vals = [r[var]["elasticity"] for r in sens_rows if var in r and np.isfinite(r[var]["elasticity"])]
        return float(np.mean(vals)) if vals else float("nan")
    sens_agg = {v: mean_elast(v) for v in ("n_eff", "excess", "ic")}
    dominant = max(sens_agg, key=lambda k: abs(sens_agg[k])) if sens_rows else None

    caus = causality_check()

    # ── P5-10 traffic light (derived from the aggregates above) ──
    slopes = [per_h[h]["calibration"].get("slope") for h in PRIMARY if per_h[h]["calibration"].get("slope") is not None]
    eces = [per_h[h]["calibration"].get("ece") for h in PRIMARY if per_h[h]["calibration"].get("ece") is not None]
    covs = [per_h[h]["metrics"].get("coverage") for h in core.VAL_HORIZONS if per_h[h]["metrics"].get("coverage") is not None]
    skills = [per_h[h]["naive"].get("skill_vs_zero") for h in PRIMARY if per_h[h]["naive"].get("skill_vs_zero") is not None]
    era_skills = [era["6m"][e].get("mae") for e in core.ERAS if era["6m"][e].get("mae") is not None]

    def light_cal():
        if not slopes or not eces:
            return "🟠"
        ms = np.mean([abs(sl - 1) for sl in slopes]); me = np.mean(eces)
        if ms < 0.3 and me < 0.05: return "🟢"
        if ms < 0.5 and me < 0.10: return "🟡"
        return "🟠"

    def light_cov():
        if not covs: return "🟠"
        d = np.mean([abs(c - 95) for c in covs])
        return "🟢" if d < 5 else "🟡" if d < 12 else "🟠"

    def light_stat():
        # non-stationarity: dispersion of era MAE relative to its mean
        if len(era_skills) < 2: return "🟠"
        cv = np.std(era_skills) / (np.mean(era_skills) + 1e-9)
        return "🟢" if cv < 0.15 else "🟡" if cv < 0.35 else "🟠"

    def light_mult():
        if not fdr_summary: return "🟠"
        surv, exp = fdr_summary["n_survive"], fdr_summary["expected_fp"]
        if surv > 3 * exp: return "🟢"
        if surv > exp: return "🟡"
        return "🟠"

    def light_skill():
        if not skills: return "🟠"
        m = np.mean(skills)
        return "🟢" if m > 0.10 else "🟡" if m > 0.02 else "🟠"

    tl = {
        "motor_causal": "🟢" if caus["passes"] else "🔴",
        "calibracion": light_cal(),
        "cobertura": light_cov(),
        "no_estacionariedad": light_stat(),
        "multiples_comparaciones": light_mult(),
        "skill_vs_ingenuo": light_skill(),
    }

    results = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_version": "régimen v1 · Score causal (percentil expandido) · 3 ejes equipeso · analogías por régimen",
        "assets": ok, "n_assets": len(ok), "horizons": core.VAL_HORIZONS,
        "config": {"MIN_ANALOG": core.MIN_ANALOG, "STEP": core.STEP, "YEARS": core.YEARS,
                   "note": "walk-forward causal estricto: análogo j válido sólo si j+h<=t"},
        "per_horizon": per_h, "era": era, "regime": reg,
        "fdr": {"summary": fdr_summary, "items": sorted(fdr_items, key=lambda x: x["p"])},
        "sensitivity": {"per_asset": sens_rows, "mean_elasticities": sens_agg, "dominant": dominant},
        "causality": caus,
        "traffic_light": tl,
        "runtime_s": round(time.time() - t0, 1),
    }
    with open(OUT, "w") as f:
        json.dump(results, f, indent=2, default=float)
    print(f"\nSaved {OUT}  ({time.time()-t0:.1f}s, {len(ok)} assets)")
    # compact console summary
    print("\nTRAFFIC LIGHT:", tl)
    for h in PRIMARY:
        m = per_h[h]["metrics"]; c = per_h[h]["calibration"]; nv = per_h[h]["naive"]
        print(f"  {h}: MAE={m['mae']:.3f} cov={m['coverage']:.0f}% bias={m['bias']:+.3f} "
              f"slope={c.get('slope',float('nan')):.2f} ECE={c.get('ece',float('nan')):.3f} "
              f"skill_vs_zero={nv.get('skill_vs_zero',float('nan')):+.3f} "
              f"sharpe={m['sharpe']:.2f} vs BH={nv.get('sharpe_buyhold',float('nan')):.2f}")
    if fdr_summary:
        print(f"  FDR: {fdr_summary['n_raw_sig']} raw-sig, exp_FP={fdr_summary['expected_fp']:.1f}, "
              f"{fdr_summary['n_survive']} survive BH")
    print("  sensitivity dominant:", dominant, sens_agg)


if __name__ == "__main__":
    main()
