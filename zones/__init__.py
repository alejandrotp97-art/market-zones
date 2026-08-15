"""market-zones: a composite 'cheapness' index that scores an asset 0-100 and
maps it to a buy/sell zone (Capitulación -> Euforia).
"""
from .engine import Summary, analyze
from .classify import BOUNDS, NAMES, raw_zone
from .data import BadSymbol, NoHistory, fetch_daily, safe_symbol

__all__ = ["analyze", "Summary", "fetch_daily", "NoHistory", "BadSymbol",
           "safe_symbol", "NAMES", "BOUNDS", "raw_zone"]
