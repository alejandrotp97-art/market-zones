"""PRE-REGISTERED success criteria for Regime Engine v2 (P7).

Written BEFORE any result is seen. These thresholds are the contract: a variant
that does not clear them is discarded, and if NO variant clears the ranking +
portfolio bars, the scientifically valid conclusion is *do not build v2*.

Rationale for each bar is documented so an auditor can challenge it up front.
"""

# ── Ranking (E1) ──
RANK_IC_MIN = 0.03          # mean cross-sectional Spearman IC per rebalance.
                            # 0.03 is a low but real bar; published equity factors
                            # live at 0.02-0.05. Below this there is no ordering skill.
IC_TSTAT_MIN = 2.0          # mean(IC)/SE(IC) — the signal must be statistically != 0.
IC_ERA_POSITIVE_MIN = 3     # IC must be positive in at least 3 of the 4 P5 eras (stability).

# ── Portfolio (E2) — the decider ──
LS_SHARPE_MIN = 0.30        # long-short (top20 - bottom20) annualised Sharpe, NET of costs.
TOP_ALPHA_MIN = 0.0         # top20 must out-return equal-weight (alpha > 0) net of costs.
MONOTONIC_REQUIRED = True   # top20 >= equal-weight >= bottom20 in mean return (ordering holds).

# ── Probability (E3) ──
BRIER_SKILL_MIN = 0.0       # 1 - Brier/Brier_climatology > 0  (must beat the base rate).
ECE_MAX = 0.05              # expected calibration error ceiling.
LOGLOSS_SKILL_MIN = 0.0     # must beat the constant-base-rate log loss.

# ── Coverage (E4) ──
COVERAGE_MIN = 0.90         # realised OOS coverage of the 95% interval must reach >=90%
COVERAGE_MAX = 0.985        # ...and not be trivially wide (>98.5% => uninformative).

# ── Costs ──
COST_BPS = 10.0             # per-side transaction cost per rebalance leg (bps of turnover).

# ── E10 scoring weights (only among variants that already PASS the gates) ──
SCORE_WEIGHTS = {
    "ranking": 0.30, "calibration": 0.15, "coverage": 0.12,
    "sharpe": 0.25, "stability": 0.10, "complexity": 0.04, "interpretability": 0.04,
}

# Higher complexity / lower interpretability are penalised; fixed priors per variant.
VARIANT_COMPLEXITY = {"A_discrete": 0.2, "euclid_knn": 0.4, "C_kernel": 0.6, "B_mahalanobis": 0.8}
VARIANT_INTERPRET = {"A_discrete": 0.9, "euclid_knn": 0.6, "C_kernel": 0.5, "B_mahalanobis": 0.4}


def gate(variant_metrics: dict) -> dict:
    """Return pass/fail per gate + overall for one variant's aggregate metrics."""
    m = variant_metrics
    g = {
        "rank_ic": (m.get("rank_ic", -9) >= RANK_IC_MIN and m.get("ic_tstat", 0) >= IC_TSTAT_MIN
                    and m.get("ic_eras_positive", 0) >= IC_ERA_POSITIVE_MIN),
        "portfolio": (m.get("ls_sharpe", -9) >= LS_SHARPE_MIN and m.get("top_alpha", -9) > TOP_ALPHA_MIN
                      and (m.get("monotonic", False) or not MONOTONIC_REQUIRED)),
        "probability": (m.get("brier_skill", -9) >= BRIER_SKILL_MIN and m.get("ece", 9) <= ECE_MAX),
        "coverage": (COVERAGE_MIN <= m.get("cov_conformal", 0) <= COVERAGE_MAX),
    }
    # v2 is justified only if ranking AND portfolio both pass (the core hypothesis).
    g["v2_worthy"] = g["rank_ic"] and g["portfolio"]
    return g
