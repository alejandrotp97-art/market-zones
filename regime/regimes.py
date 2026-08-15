"""Regime state machine (region map v1) with hysteresis + minimum dwell.

The regime is a REGION in the 3-axis space (level × volatility × trend, with
instability as a tie-breaker), not a band of the score. Splits are distributional
(percentile terciles / spike thresholds), NOT tuned free parameters.

NOTE: the region *thresholds* are the architecture's v1 proposal; the SCORE they
sit on is fully validated (FASE 1-3), but these regime cut-offs still owe a
stress-window calibration (2008 / 2020 / laterales…). Treated as provisional.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

PANICO = "Pánico"
CAPITULACION = "Capitulación"
RECUPERACION = "Recuperación"
ALCISTA = "Alcista sano"
SOBRECAL = "Sobrecalentamiento"
CLIMAX = "Clímax"
DISTRIBUCION = "Distribución"
CORRECCION = "Corrección"
LATERAL = "Lateral"

REGIMES = [PANICO, CAPITULACION, RECUPERACION, ALCISTA, SOBRECAL, CLIMAX,
           DISTRIBUCION, CORRECCION, LATERAL]
SHOCK = {PANICO, CLIMAX}          # may trigger without waiting for dwell


def raw_regime(L: float, V: float, I: float, up: float, calibrated: bool = True) -> Optional[str]:
    """Instantaneous region, priority-ordered (shocks first). L/V/I in 0-100, up in {0,1}.

    calibrated=True (v2, adopted): shocks require an EXTREME vol percentile (a spike,
    V≥90), so a grinding bear reads Corrección/Capitulación, not chronic Pánico; and
    Lateral requires a CALM tape (V≤50), so it cannot fire during a high-vol crash.
    calibrated=False reproduces the v1 thresholds for comparison.
    """
    if not all(np.isfinite(v) for v in (L, V, I, up)):
        return None
    upt = up >= 0.5

    if not calibrated:                       # ---- v1 (pre-calibration) ----
        if L >= 80 and V >= 75: return CLIMAX
        if L <= 25 and V >= 80: return PANICO
        if L <= 20 and V >= 55: return CAPITULACION
        if I >= 70 and 30 <= L <= 70: return LATERAL
        if L >= 80: return SOBRECAL
        if L >= 66 and not upt: return DISTRIBUCION
        if upt:
            if L >= 50: return ALCISTA
            if L >= 25: return RECUPERACION
            return CAPITULACION
        if L >= 55: return CORRECCION
        if L <= 30: return CAPITULACION
        return LATERAL

    # ---- v2 (calibrated) ----
    if L >= 85 and V >= 85:                   # euphoric blow-off
        return CLIMAX
    if L <= 25 and V >= 90:                   # acute panic = vol SPIKE, not chronic
        return PANICO
    if L <= 15:                              # deep value bottoms out as capitulation
        return CAPITULACION
    if L <= 25 and V >= 55:
        return CAPITULACION
    if I >= 70 and V <= 50 and 25 <= L <= 75:  # choppy AND calm = truly lateral
        return LATERAL
    if L >= 80:
        return SOBRECAL
    if L >= 66 and not upt:
        return DISTRIBUCION
    if upt:
        if L >= 50:
            return ALCISTA
        if L >= 25:                          # a bounce that hasn't cleared 25 is
            return RECUPERACION               # still capitulation territory
        return CAPITULACION
    if L >= 55:
        return CORRECCION
    if L <= 30:
        return CAPITULACION
    return LATERAL


def classify_series(L, V, I, up, dwell: int = 5, calibrated: bool = True) -> list[Optional[str]]:
    """Smooth the raw regions with minimum-dwell hysteresis: a non-shock switch
    requires the new region to persist `dwell` days; shocks switch immediately."""
    cur: Optional[str] = None
    cand: Optional[str] = None
    cc = 0
    out: list[Optional[str]] = []
    for i in range(len(L)):
        raw = raw_regime(L[i], V[i], I[i], up[i], calibrated)
        if raw is None:
            out.append(cur)
            continue
        if cur is None or raw == cur:
            cand, cc = None, 0
            cur = raw if cur is None else cur
        else:
            cc = cc + 1 if raw == cand else 1
            cand = raw
            if raw in SHOCK or cc >= dwell:
                cur, cand, cc = raw, None, 0
        out.append(cur)
    return out


def dwell_days(regimes: list) -> int:
    if not regimes or regimes[-1] is None:
        return 0
    last, n = regimes[-1], 0
    for r in reversed(regimes):
        if r == last:
            n += 1
        else:
            break
    return n
