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
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


def _series(n=300, px=100.0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.Series([px] * n, index=idx)


@pytest.fixture(autouse=True)
def _restore_real_fetchers():
    """_stub replaces module functions; put the real ones back after each test so
    later test files do not inherit the stubs."""
    real = D._close_series, D._quote_meta
    yield
    D._close_series, D._quote_meta = real


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


def test_the_leveraged_gold_line_has_a_sibling_in_milan():
    """Gestora Delta Gold 3x on Stuttgart answers a live quote and NO daily bars at
    all: it is priced but cannot be charted. `OROX3.MI` is the same instrument on
    Borsa Italiana, same currency, quoted 0.5% away — inside the gate below."""
    assert D.HISTORY_PROXY["IE00BZ3XGLD1.SG"] == "OROX3.MI"


def test_the_milan_sibling_of_the_leveraged_gold_passes_the_gate():
    """Measured against the user's own SG fills (ratios 1.0008 / 1.0115 /
    0.9773): the two plazas arbitrage each other, so the deviation is a venue
    spread, not a different asset."""
    _stub({"OROX3.MI": _series(px=118.31)},
          {"IE00BZ3XGLD1.SG": (117.75, "EUR"), "OROX3.MI": (118.31, "EUR")})
    s, dev = D._proxy_series("IE00BZ3XGLD1.SG", "OROX3.MI")
    assert s is not None
    assert dev < 0.005, f"deviation {dev}"



# ── las dos plazas no baten al mismo ritmo ────────────────────────────────
def _rango(valores):
    """Una serie con rango de verdad: lo que se negoció, no un precio fijo."""
    idx = pd.bdate_range("2024-01-01", periods=len(valores))
    return pd.Series([float(v) for v in valores], index=idx)


def test_un_desvio_por_retraso_de_plaza_no_saca_al_instrumento():
    """Lo que le pasó al oro 3x un viernes: Milán cierra con su cierre del día
    y la cotización de Stuttgart, mucho más líquida a ratos, sigue imprimiendo
    operaciones atrasadas. Los dos precios son del MISMO instrumento y aun así
    se quedan 3 puntos separados, que es más del doble del umbral: con la
    regla estricta la posición desaparecía de la historia, la correlación y las
    zonas según la hora a la que se abriera el panel."""
    _stub({"OROX3.MI": _rango([123.02, 125.25, 111.59, 114.95, 114.38, 114.25])},
          {"IE00BZ3XGLD1.SG": (117.75, "EUR"), "OROX3.MI": (114.25, "EUR")})

    s, dev = D._proxy_series("IE00BZ3XGLD1.SG", "OROX3.MI")

    assert s is not None, "un retraso de plaza no debería sacar al instrumento"
    assert dev == pytest.approx(abs(114.25 / 117.75 - 1)), dev


def test_un_instrumento_distinto_no_entra_por_la_ventana():
    """La misma admisión, pero contra lo que el proxy NEGOCIA de verdad. La otra
    clase de participación cotiza a mitad de precio y tiene rango de sobra: ni
    con rango ni sin él su precio cae dentro de la ventana."""
    _stub({"P": _rango([70.0, 72.0, 74.0, 76.0, 78.0, 80.0])},
          {"O": (139.92, "EUR"), "P": (74.7, "EUR")})

    s, why = D._proxy_series("O", "P")

    assert s is None and "desvío" in why, why


def test_la_ventana_de_hoy_cuenta_tambien(monkeypatch):
    """La serie propia del proxy ya dice dónde ha estado; lo cotizado HOY dice
    dónde está ahora. Las dos cosas juntas son la ventana."""
    _stub({"P": _rango([114.25] * 6)},
          {"O": (117.75, "EUR"), "P": (114.25, "EUR")})
    monkeypatch.setattr(D, "_ventana", lambda t: (113.57, 121.0))

    s, dev = D._proxy_series("O", "P")

    assert s is not None, "el rango de hoy debería contar"
    assert dev == pytest.approx(abs(114.25 / 117.75 - 1)), dev


def test_sin_ventana_y_sin_rango_la_barra_sigue_en_su_sitio():
    """Si no hay nada con qué ensanchar la comparación, manda el umbral: la
    comprobación estricta es el caso por defecto, no la excepción."""
    _stub({"P": _rango([114.25] * 6)},
          {"O": (117.75, "EUR"), "P": (114.25, "EUR")})

    s, why = D._proxy_series("O", "P")

    assert s is None and "desvío" in why, why



# ── the ONE helper every consumer of a series goes through ───────────────
def test_its_own_series_is_returned_as_its_own():
    """`_close_series` stays PURE: it never hands back a borrowed series, or the
    disclosure the resolver and the chart make about whose series it is would
    be a guess."""
    _stub({"BTC-EUR": _series(), "OROX3.MI": _series(px=118.31)},
          {"BTC-EUR": (76000.0, "EUR"), "OROX3.MI": (118.31, "EUR")})
    s, via, motivo = D._serie_de("BTC-EUR")
    assert s is not None and via is None and motivo is None


def test_a_ticker_with_no_proxy_at_all_comes_back_empty():
    _stub({}, {"SINHISTORICO": (100.0, "EUR")})
    assert D._serie_de("SINHISTORICO") == (None, None, None)


def test_a_refused_proxy_says_who_refused_and_why():
    """The reason travels with the symbol: «sin histórico» and «half the price»
    are different facts, and only the second one is worth going to look at."""
    _stub({"OROX3.MI": _series(px=74.7)},
          {"IE00BZ3XGLD1.SG": (117.75, "EUR"), "OROX3.MI": (74.7, "EUR")})
    s, via, motivo = D._serie_de("IE00BZ3XGLD1.SG")
    assert s is None and via == "OROX3.MI" and "desvío" in motivo, motivo


def test_a_proxy_that_passes_is_returned_with_the_symbol_it_came_from():
    _stub({"OROX3.MI": _series(px=118.31)},
          {"IE00BZ3XGLD1.SG": (117.75, "EUR"), "OROX3.MI": (118.31, "EUR")})
    s, via, motivo = D._serie_de("IE00BZ3XGLD1.SG")
    assert s is not None and via == "OROX3.MI" and motivo is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    import importlib
    for f in fns:
        importlib.reload(D)          # each test restubs from a clean module
        f()
        print("PASS", f.__name__)
    importlib.reload(D)
    print(f"\n{len(fns)} passed")
