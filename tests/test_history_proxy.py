"""Borrowing a sibling listing's price series.

Two listings of the same ISIN, in the same currency, arbitraged against each
other, are the same asset seen through another window — not an approximation.
But the substitution is only sound while it keeps agreeing, so every condition
is re-checked at run time rather than trusted from the table.

The failure this must never produce: charting a DIFFERENT instrument silently.
Yahoo lists GOLD.PA under the same ISIN as GOLD.MI, with MORE history, at about
half the price — a different share class. That is why the mapping is hand-made
and gated, not discovered.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


def _series(n=300, px=100.0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.Series([px] * n, index=idx)


def _stub(series_of, meta_of):
    D._close_series = lambda t: series_of.get(t)
    D._quote_meta = lambda t: meta_of.get(t, (None, None))


def test_a_sound_sibling_is_accepted():
    _stub({"P": _series(px=140.0)},
          {"O": (139.92, "EUR"), "P": (140.0, "EUR")})
    s, dev = D._proxy_series("O", "P")
    assert s is not None
    assert dev < 0.001, f"deviation {dev}"


def test_a_different_share_class_is_refused():
    """The GOLD.PA case: same ISIN, more history, half the price."""
    _stub({"P": _series(px=74.7)},
          {"O": (139.92, "EUR"), "P": (74.7, "EUR")})
    s, why = D._proxy_series("O", "P")
    assert s is None and "desvío" in why, why


def test_a_different_currency_is_refused():
    """Same instrument, other currency, would need an FX conversion the chart
    does not apply to a borrowed series — refuse rather than distort."""
    _stub({"P": _series(px=161.57)},
          {"O": (139.92, "EUR"), "P": (161.57, "USD")})
    s, why = D._proxy_series("O", "P")
    assert s is None and "divisa distinta" in why, why


def test_a_proxy_without_history_is_refused():
    _stub({}, {"O": (139.92, "EUR"), "P": (140.0, "EUR")})
    s, why = D._proxy_series("O", "P")
    assert s is None and "tampoco tiene histórico" in why, why


def test_a_missing_quote_is_refused_not_assumed():
    """Without both prices there is nothing to compare, so the substitution is
    unverified — and an unverified proxy is exactly what this gate exists for."""
    _stub({"P": _series()}, {"O": (None, "EUR"), "P": (140.0, "EUR")})
    assert D._proxy_series("O", "P")[0] is None
    _stub({"P": _series()}, {"O": (139.9, None), "P": (140.0, "EUR")})
    assert D._proxy_series("O", "P")[0] is None


def test_the_boundary_is_where_it_says():
    for dev, expected in ((0.019, True), (0.021, False)):
        _stub({"P": _series(px=100 * (1 + dev))},
              {"O": (100.0, "EUR"), "P": (100 * (1 + dev), "EUR")})
        got = D._proxy_series("O", "P")[0] is not None
        assert got is expected, f"dev={dev} accepted={got}"


def test_the_configured_map_is_shaped_correctly():
    """A typo here charts the wrong asset, so assert the table itself."""
    assert D.HISTORY_PROXY, "the map is empty"
    for orig, via in D.HISTORY_PROXY.items():
        assert orig != via
        assert isinstance(via, str) and via.strip() == via and via


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    import importlib
    for f in fns:
        importlib.reload(D)          # each test restubs from a clean module
        f()
        print("PASS", f.__name__)
    importlib.reload(D)
    print(f"\n{len(fns)} passed")
