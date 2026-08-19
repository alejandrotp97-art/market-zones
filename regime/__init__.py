"""regime — the validated minimal market-regime engine (FASE 4 spec).

Score core = 3 causal axes {mayer, realized_vol, drawdown}, expanding-percentile
normalized (no lookahead), equal-weight. Regime = region state machine.
"""
from . import regimes
from .engine import Reading, analyze

__all__ = ["Reading", "analyze", "regimes"]
