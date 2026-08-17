"""Conviction layer — the confirmation criterion that sits ON TOP of the zone.

Volatility and volume are non-monotonic with the cheap<->expensive axis (they
spike at BOTH capitulation bottoms and euphoric tops), so they must NOT be
averaged into the score. Instead they grade an EXTREME reading by how climactic
it is: a Capitulación with a volatility spike + volume climax is a high-
conviction (confirmed) bottom; a Capitulación in calm, thin tape is unconfirmed
(a slow bleed — wait). Same idea, mirrored, for Euforia.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .indicators import realized_vol, volume_spike
from .normalize import Z_MIN_PERIODS, expanding_pct_rank, pct_rank

# Zones that carry a meaningful "climax" reading (the outer/near-outer bands).
# Equilibrio (2) does not — nothing extreme to confirm.
_EXTREME = {0, 1, 3, 4}
_LABELS = {"alta": "Clímax confirmado", "media": "Parcial", "baja": "Sin confirmar"}
HI, MID = 70.0, 45.0


def _volume_usable(volume: Optional[pd.Series]) -> bool:
    if volume is None:
        return False
    v = pd.Series(volume, dtype=float)
    return len(v) > 0 and float((v > 0).mean()) > 0.8


def compute(close: pd.Series, volume: Optional[pd.Series], zones: list,
            causal: bool = False, rmin: int = Z_MIN_PERIODS,
            vol_w: int = 20, volu_w: int = 50) -> dict:
    """Return per-row conviction columns:
      vol_pct  — percentile of realized volatility (0-100)
      volu_pct — percentile of the volume spike (0-100, NaN if volume unusable)
      climax   — mean of the available percentiles (the 'how climactic' score)
      conviction — level string per row ('alta'/'media'/'baja') or None

    `causal=True` ranks each day only against the days before it, so a past
    "Clímax confirmado" chip reflects what was knowable that day.

    `vol_w`/`volu_w` son CUENTAS DE BARRAS, y deben venir del mismo preset de
    ventanas que usa el score. Cuando no se pasaban, esta capa se quedaba con
    20 y 50 barras SIEMPRE: sobre barras semanales eso miraba 20 y 50 semanas
    mientras la pata de volatilidad del score miraba 4, así que el clímax de un
    activo en semanal se calibraba a una escala que no era la suya. Los valores
    por defecto son los diarios, para que un llamante antiguo no cambie.
    """
    close = pd.Series(close, dtype=float)
    rank = (lambda s: expanding_pct_rank(s, rmin)) if causal else pct_rank
    vol_pct = rank(realized_vol(close, vol_w))
    if _volume_usable(volume):
        volu_pct = rank(volume_spike(volume, volu_w))
    else:
        volu_pct = pd.Series(np.nan, index=close.index)

    climax = pd.concat([vol_pct, volu_pct], axis=1).mean(axis=1, skipna=True)
    cvals = climax.to_numpy()
    conviction = [_level(cvals[i] if i < len(cvals) else np.nan, z)
                  for i, z in enumerate(zones)]
    return {"vol_pct": vol_pct, "volu_pct": volu_pct,
            "climax": climax, "conviction": conviction}


def _level(climax: float, zone: Optional[int]) -> Optional[str]:
    if zone is None or zone not in _EXTREME or not np.isfinite(climax):
        return None
    if climax >= HI:
        return "alta"
    if climax >= MID:
        return "media"
    return "baja"


def label(level: Optional[str]) -> Optional[str]:
    return _LABELS.get(level) if level else None
