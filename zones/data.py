"""Data adapter: daily OHLCV from Yahoo Finance via stdlib urllib.

Mirrors the working pattern used by metals-signal-bot (same endpoint, same
User-Agent). No third-party dependency: keeps the project runnable on any
interpreter that has numpy+pandas. This is the ONLY module that touches the
network — the scoring core never does.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import datetime as dt

import pandas as pd

_UA = {"User-Agent": "Mozilla/5.0"}
_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"

# Everything a real Yahoo ticker uses: letters, digits, and `. - ^ = _`.
# Covers ^RUT, BZ=F, BTC-USD, 0P0001CLDK.F, IE000M7V94E1.SG, EURUSD=X.
#
# The FIRST character must be alphanumeric or `^`, which every real ticker is.
# That is not cosmetic: `.` has to be allowed inside a symbol, and a charset
# alone would then accept `..` — a single path segment that still walks up a
# level. Anchoring the first character makes a bare traversal unrepresentable,
# and since `/` is rejected the whole symbol is one segment, so `..` cannot
# appear as a segment anywhere else either.
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9^][A-Za-z0-9._^=-]{0,31}$")


class BadSymbol(ValueError):
    """The symbol is not a ticker shape we will put in a URL."""


def safe_symbol(symbol) -> str:
    """Validate a ticker and return it percent-encoded for use in a URL PATH.

    Interpolating a raw symbol into the URL let it steer the request: `..%2F..`
    walked out of the chart endpoint into other Yahoo APIs (verified — the
    upstream answered), `?` appended query parameters and `#` truncated the
    ones we set. Two layers, because either alone is thin:

      * reject anything outside the ticker character set, so a hostile value
        never reaches the network and the caller gets a clear error;
      * percent-encode what survives, so `^` and `=` travel correctly and no
        future addition to the charset can become a separator by accident.

    Yahoo decodes the escapes server-side; `%5ERUT` and `BZ%3DF` both resolve.
    """
    s = str(symbol or "").strip()
    if not _SYMBOL_RE.match(s):
        raise BadSymbol(f"Símbolo no válido: {s[:40]!r}. Solo letras, números "
                        f"y . - ^ = _ (máx. 32 caracteres).")
    return urllib.parse.quote(s, safe="")


class NoHistory(LookupError):
    """Yahoo answered for this symbol, but with no daily bars.

    This is NOT a bad ticker and NOT a network failure: `meta` comes back
    populated (so a live quote is available) while `timestamp` is absent
    entirely. Seen on ISIN-style European listings such as `IE000M7V94E1.SG`.
    Its own exception type exists so callers can tell "priceable but not
    chartable" apart from "does not exist" — treating the two the same is how
    a position silently vanishes from a chart while its cost stays in.
    """


def fetch_daily(symbol: str, years: int = 25, drop_forming: bool = True) -> pd.DataFrame:
    """Daily OHLCV for `symbol`, closed bars only.

    Returns a frame with columns: date, open, high, low, close, volume, and
    carries Yahoo's `meta` block (longName, shortName, currency, ...) on
    `df.attrs["meta"]` — `{}` when the response omitted it. Read it BEFORE
    reshaping the frame: pandas does not guarantee `attrs` survives a resample.

    `years` bounds the lookback; Yahoo returns whatever history it has within
    that window (so a young ETF like SPCX simply comes back short).

    Raises `NoHistory` when the response carries no daily bars, and `BadSymbol`
    when `symbol` is not a ticker shape.
    """
    enc = safe_symbol(symbol)
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    p1 = now - years * 366 * 86400
    url = f"{_BASE}{enc}?period1={p1}&period2={now}&interval=1d"
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]

    ts = res.get("timestamp")
    if not ts:
        raise NoHistory(f"Yahoo no publica histórico diario para '{symbol}' "
                        f"(devuelve cotización pero no serie de precios)")
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "open": q["open"], "high": q["high"], "low": q["low"],
        "close": q["close"], "volume": q["volume"],
    }, index=pd.to_datetime(ts, unit="s", utc=True))
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["date"] = df.index

    if drop_forming:
        today = pd.Timestamp.now(tz="utc").normalize()
        df = df[df["date"].dt.normalize() < today]

    out = df.reset_index(drop=True)
    # `meta` rides along in THIS same response and used to be dropped on the
    # floor, so the dashboard fetched it again from the SAME endpoint: a 12s
    # timeout chained behind the 30s above, i.e. 42s worst case against a 25s
    # client abort, and a failure there was never cached. Exposing it here costs
    # nothing and leaves the return contract (the frame and its columns) intact.
    out.attrs["meta"] = res.get("meta") or {}
    return out
