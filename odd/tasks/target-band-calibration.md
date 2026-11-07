# target-band-calibration — better-calibrated target price range

Objective: improve the forecast of the target range (consensus + band) shown in "Precio objetivo por zona", shipping only the ideas that beat the current method out-of-sample. Branch `feat/target-band-calibration` from `main`.
Delivery: ask-on-risk. Test runner: `.venv/bin/python -m pytest -q`.

## Specs

- S1 — Goal (L2): "Trabaja en las ideas que necesites e implementa las ideas que te hayan dado resultado positivo. Necesitamos mejorar ese pronostico". "Ese pronóstico" = the target range: the buy/sell consensus and its band (`zones/target.py`, `K_BUY`/`K_SELL`).
- S2 — Positive result means: better than the current band on a causal walk-forward (each fit uses only samples whose forward window had already closed), measured with a declared metric (pinball loss of the stated quantile + empirical coverage). In-sample gains do not count.
- S3 — Ideas that do not beat the current method are documented in research and NOT shipped.
- S4 — Any shipped change keeps the panel contract (`compute()` keys) and updates `research/band_calibration/README.md` with the new values and the date.

Assumption: the band keeps its meaning (typical depth into the zone, P50 among touches) unless a study shows a different quantile is better defined.

## Tasks

| ID | Specs | Route | Status | Evidence |
|----|-------|-------|--------|----------|
| T1 | S1,S3 | inline | done | MC touch study committed 178e2cf: not calibrated enough → not shipped |
| T2 | S2 | inline | done | `band_walkforward.py run`: 1571 as-ofs, 19 assets (+ETH-USD), vol 63/252/756 at the as-of; tests RED→GREEN |
| T3 | S2,S3 | inline | done | no band candidate beats the shipped k (see L4-L6) → band NOT changed |
| T5 | S1,S2 | inline | done | cc51767: M1 beats the median consensus OOS (L7-L8); tests RED→GREEN |
| T4 | S1,S4 | inline | done | 8eebc05: headline = M1 (M3 fallback), key `consensus` kept; band K unchanged; tests RED→GREEN, suite green, card checked on a temp instance :8791 |

## Log

- L1 (2026-11-07) — "Entonces que hacemos para calibrar aun mejor el rango objetivo"
- L2 (2026-11-07) — "Trabaja en las ideas que necesites e implementa las ideas que te hayan dado resultado positivo. Necesitamos mejorar ese pronostico"
- L3 — Weakness found: `band_prototype_data.py` fits k against each asset's full-history AVERAGE vol but `target.py` applies it to the LAST-YEAR vol; 18 points (one per asset), crypto = 1. No out-of-sample check of the band per as-of exists.
- L4 — Band walk-forward (OOS, pinball q=0.5): buy current 0.0728 vs global refit 0.0745, per-class 0.0804, no-vol 0.0791, power a*vol^b 0.0734 (vol_63 power 0.0723, -0.7%: noise). Sell current 0.3193 beats every candidate. Refit k with as-of vol = 0.62 / 1.16: the shipped values hold.
- L5 — Extra factors k*vol*z^g (vratio, sell distance, buy distance): buy -1.6% to -5.7%; sell s_dist +1.5% but coverage 0.49→0.40 and worse in quality → rejected.
- L6 — Recency-window refit (last 4/6/8/10 years): worse than shipped on both sides. Conclusion: band unchanged; the open lever is the target level itself (T5).
- Engram mirror pending: mem_save returned ambiguous_project (several git repos under the cwd).
- L7 — Target vs real zone entry (OOS mean |log err|): buy 0.113 M1 / 0.149 M3 / 0.119 cons; buy raw 0.106 / 0.169 / 0.127; sell 0.066 / 0.112 / 0.080; sell raw 0.056 / 0.110 / 0.071. Fitted blend gives M1 0.85-1.00 of the weight.
- L8 — Robustness (bootstrap by asset, 2006+): sell and buy-raw significant, 16/19 assets, both eras; buy on the shown score neutral (+0.005, CI includes 0). BTC/ETH alone mixed, n 8-27 (noise).
- L9 — Band anchored on M1: shipped k still beats the refit OOS (buy 0.0722 vs 0.0750; sell 0.337 vs 0.339) → K_BUY/K_SELL unchanged. Live service NOT restarted: deploy is the user's call.
