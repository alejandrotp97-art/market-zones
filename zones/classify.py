"""Score -> zone classification with 2-point hysteresis, and the plain-language
verdict that mirrors the dashboard's "Qué dice el índice" panel.
"""
from __future__ import annotations

import bisect
from typing import Optional

import numpy as np
import pandas as pd

# Zone boundaries and names. Zone i occupies [BOUNDS[i-1], BOUNDS[i]).
BOUNDS = [20.0, 40.0, 60.0, 80.0]
NAMES = ["Capitulación", "Acumulación", "Equilibrio", "Precaución", "Euforia"]
HYSTERESIS = 2.0


def raw_zone(score: float) -> int:
    """Zone index a score maps to with no hysteresis. [0,20)->0 ... [80,100]->4."""
    return bisect.bisect_right(BOUNDS, score)


def classify_series(score: pd.Series, hysteresis: float = HYSTERESIS) -> list[Optional[int]]:
    """Assign a zone index to each score, applying hysteresis so the zone does
    not flip at an exact boundary — the score must overshoot the boundary by
    `hysteresis` points to switch. NaN scores (warm-up) map to None.

    The first finite score seeds the state with its raw zone; from there the
    zone only moves up when score >= upper_boundary + h, or down when
    score < lower_boundary - h. Multi-zone jumps are handled by climbing/
    descending one boundary at a time.
    """
    cur: Optional[int] = None
    zones: list[Optional[int]] = []
    for s in score.to_numpy():
        if not np.isfinite(s):
            zones.append(None)
            continue
        if cur is None:
            cur = raw_zone(s)
        else:
            target = raw_zone(s)
            # Move up while the score clears the next upper boundary + margin.
            while target > cur and s >= BOUNDS[cur] + hysteresis:
                cur += 1
            # Move down while the score falls below the current lower boundary - margin.
            while target < cur and s < BOUNDS[cur - 1] - hysteresis:
                cur -= 1
        zones.append(cur)
    return zones


def dwell_days(zones: list[Optional[int]]) -> int:
    """How many consecutive most-recent rows share the latest zone."""
    if not zones or zones[-1] is None:
        return 0
    last, n = zones[-1], 0
    for z in reversed(zones):
        if z == last:
            n += 1
        else:
            break
    return n


def verdict(zone: Optional[int], stretch=None, rsi=None, drawdown=None,
            trend_dev=None, dwell: int = 0) -> str:
    """Human-readable read of the index, in the reference product's Spanish,
    flagging whichever components are currently extreme.
    """
    if zone is None:
        return "Histórico insuficiente para clasificar."
    base = {
        0: "El precio cotiza muy por debajo de su media larga y su tendencia.",
        1: "El precio está barato respecto a su media y su tendencia.",
        2: "El precio se mueve en torno a su media y su tendencia.",
        3: "El precio cotiza por encima de su media y su tendencia.",
        4: "El precio está muy estirado por encima de su media y su tendencia.",
    }[zone]
    flags = []
    if rsi is not None and np.isfinite(rsi):
        if rsi < 35:
            flags.append("RSI deprimido")
        elif rsi > 65:
            flags.append("RSI sobrecomprado")
    if drawdown is not None and np.isfinite(drawdown) and drawdown < 30:
        flags.append("caída profunda desde máximos")
    if stretch is not None and np.isfinite(stretch):
        if stretch < 25:
            flags.append("lejos por debajo de la MA200")
        elif stretch > 75:
            flags.append("muy por encima de la MA200")
    tail = f" ({', '.join(flags)})" if flags else ""
    since = f" Desde hace {dwell} días." if dwell else ""
    return f"{base}{tail}.{since}"
