"""market-zones: a composite 'cheapness' index that scores an asset 0-100 and
maps it to a buy/sell zone (Capitulación -> Euforia).
"""
from .classify import BOUNDS, NAMES, raw_zone
from .data import BadSymbol, NoHistory, fetch_daily, safe_symbol
from .engine import DAILY, WEEKLY, Summary, Windows, analyze
from .resample import to_weekly

__all__ = [
           "BOUNDS",
           "DAILY",
           "NAMES",
           "WEEKLY",
           "BadSymbol",
           "NoHistory",
           "Summary",
           "Windows",
           "analyze",
           "fetch_daily",
           "raw_zone",
           "safe_symbol",
           "to_weekly",
]
