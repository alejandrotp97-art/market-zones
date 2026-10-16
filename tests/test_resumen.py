"""Resumen de un mes o un año («Rewind»): rentabilidad, índice, lo mejor, lo peor,
dividendos y aportaciones del periodo.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D
from cartera import periods


def _pos(valores, flujos, divs=None):
    n = len(valores)
    return {"value": np.array(valores, float), "flows": np.array(flujos, float),
            "divs": np.array(divs or [0.0] * n, float)}


def test_range_returns_incluye_lo_vendido_dentro_del_rango():
    """Una posición vendida a mitad de mes también hizo algo ese mes."""
    pos = {"A": _pos([100, 110, 0, 0], [100, 0, -120, 0]),
           "B": _pos([0, 0, 0, 0], [0, 0, 0, 0])}
    r = periods.range_returns(pos, 0, 3)
    assert r["A"]["abs"] == pytest.approx(20.0)        # 0 − 100 + 120
    assert "B" not in r


def test_range_returns_desde_el_principio():
    pos = {"A": _pos([100, 110, 121], [100, 0, 0])}
    assert periods.range_returns(pos, None, 2)["A"]["abs"] == pytest.approx(21.0)
    assert periods.range_returns(pos, None, 1)["A"]["abs"] == pytest.approx(10.0)


# ── la ruta ───────────────────────────────────────────────────────────────
@pytest.fixture
def libro(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "CARTERA_DB", str(tmp_path / "cartera.db"))
    monkeypatch.setattr(D, "_instrument_ccy", lambda s: "EUR")
    monkeypatch.setattr(D, "_fx_series_eur", lambda c: None)
    monkeypatch.setattr(D, "_fx_now", lambda c: 1.0)
    monkeypatch.setattr(D, "_last_price", lambda t: 120.0)
    monkeypatch.setattr(D, "_seed_geo_async", lambda *a, **k: None)
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    monkeypatch.setattr(D, "PUBLIC_HOST", "")
    return D.app.test_client()


def _post(cli, **body):
    r = cli.post("/api/cartera", json=body, headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 200, r.get_data(as_text=True)


@pytest.fixture
def con_historia(libro, monkeypatch):
    """Enero y febrero de 2024. AAA sube 10 % en febrero, BBB baja 5 %; el índice
    sube 2 %. Cobro de 7 € de AAA y compra de 100 € más de BBB en febrero."""
    idx = pd.bdate_range("2024-01-02", "2024-02-29")
    feb = idx >= "2024-02-01"
    n_feb = int(feb.sum())
    def serie(ini, fin):
        v = np.full(len(idx), float(ini))
        v[feb] = np.linspace(ini, fin, n_feb + 1)[1:]
        return pd.Series(v, index=idx)
    s = {"AAA": serie(100, 110), "BBB": serie(100, 95), "IDX": serie(50, 51)}
    monkeypatch.setattr(D, "_close_series", lambda t: s.get(t))
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-02", name="Alfa")
    _post(libro, ticker="BBB", side="buy", quantity=10, price=100, date="2024-01-02", name="Beta")
    _post(libro, ticker="AAA", side="div", price=7, date="2024-02-15")
    d0 = idx[feb][0]
    _post(libro, ticker="BBB", side="buy", quantity=1, price=100, date=d0.date().isoformat())
    return libro


def test_resumen_de_un_mes(con_historia):
    d = con_historia.get("/api/cartera/resumen?periodo=2024-02&benchmark=IDX").get_json()
    assert d["periodo"] == "2024-02"
    assert d["best"]["ticker"] == "AAA" and d["best"]["name"] == "Alfa"
    assert d["best"]["abs"] == pytest.approx(100 + 7, abs=0.5)
    assert d["worst"]["ticker"] == "BBB"
    assert d["benchmark"] == pytest.approx(0.02, abs=1e-6)
    assert d["dividends"] == pytest.approx(7.0)
    assert d["net_contributions"] == pytest.approx(100.0)
    assert d["twr"] > 0
    assert "2024-01" in d["available"]["months"] and 2024 in d["available"]["years"]


def test_resumen_de_un_anyo_por_defecto_el_ultimo_mes(con_historia):
    d = con_historia.get("/api/cartera/resumen?periodo=2024&benchmark=IDX").get_json()
    assert d["periodo"] == "2024" and d["from"] == "2024-01-02"
    d2 = con_historia.get("/api/cartera/resumen?benchmark=IDX").get_json()
    assert d2["periodo"] == "2024-02"


def test_resumen_periodo_raro_es_400(con_historia):
    assert con_historia.get("/api/cartera/resumen?periodo=2024-13").status_code == 400
    assert con_historia.get("/api/cartera/resumen?periodo=abc").status_code == 400


def test_resumen_fuera_del_historico_es_404(con_historia):
    assert con_historia.get("/api/cartera/resumen?periodo=2019-05&benchmark=IDX").status_code == 404


def test_resumen_sin_movimientos(libro):
    assert libro.get("/api/cartera/resumen").get_json()["empty"] is True
