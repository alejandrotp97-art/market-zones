"""Mapa de calor de rentabilidad mensual: años × meses.

`cartera.returns.monthly_twr` encadena los MISMOS tramos diarios que el TWR del
panel, agrupados por mes; `monthly_grid` los coloca en la cuadrícula. Lo que se
protege: que la aportación no cuente como ganancia, que el año cuadre con sus
meses, y que un mes sin datos salga vacío y no a cero.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D
from cartera.returns import monthly_grid, monthly_twr, twr


def test_cada_mes_encadena_sus_dias_y_la_aportacion_no_cuenta():
    dates = list(pd.to_datetime(["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-29"]))
    vals = [100.0, 110.0, 1210.0, 1331.0]
    flows = [100.0, 0.0, 1100.0, 0.0]          # el 1 de febrero entran 1100 €
    m = monthly_twr(vals, flows, dates)
    assert [x["month"] for x in m] == ["2024-01", "2024-02"]
    assert m[0]["ret"] == pytest.approx(0.10)
    assert m[1]["ret"] == pytest.approx((1210 - 1100) / 110 * 1331 / 1210 - 1)


def test_el_anyo_de_la_cuadricula_es_el_mismo_que_el_del_twr():
    idx = pd.bdate_range("2023-11-01", "2024-03-29")
    vals = [100.0 * (1.001 ** i) for i in range(len(idx))]
    flows = [0.0] * len(idx)
    flows[0] = 100.0
    g = monthly_grid(monthly_twr(vals, flows, list(idx)))
    by_year = {r["year"]: r["ret"] for r in twr(vals, flows, list(idx))["by_year"]}
    assert [y["year"] for y in g] == [2023, 2024]
    for y in g:
        assert y["total"] == pytest.approx(by_year[y["year"]])


def test_mes_sin_datos_es_none_y_no_cero():
    dates = list(pd.to_datetime(["2024-01-30", "2024-01-31", "2024-03-01", "2024-03-04"]))
    m = monthly_twr([100.0, 101.0, 101.0, 102.0], [100.0, 0, 0, 0], dates)
    g = monthly_grid(m)
    assert g[0]["months"][1] is None              # febrero
    assert g[0]["months"][0] == pytest.approx(0.01)
    assert len(g[0]["months"]) == 12


def test_monthly_twr_corto_devuelve_vacio():
    assert monthly_twr([100.0], [100.0], [pd.Timestamp("2024-01-01")]) == []


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


def test_ruta_mensual_trae_cartera_e_indice(libro, monkeypatch):
    end = pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=80)
    cartera = pd.Series([100.0 + i for i in range(80)], index=idx)
    indice = pd.Series([50.0] * 80, index=idx)
    monkeypatch.setattr(D, "_close_series", lambda t: indice if t == "IDX" else cartera)
    r = libro.post("/api/cartera", json={"ticker": "AAA", "side": "buy", "quantity": 1,
                                         "price": 100, "date": idx[0].date().isoformat()},
                   headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 200

    d = libro.get("/api/cartera/mensual?benchmark=IDX").get_json()

    assert d["benchmark_ticker"] == "IDX"
    ultimo = d["years"][-1]
    assert len(ultimo["months"]) == 12 and len(ultimo["bench"]) == 12
    mes = idx[-1].month - 1
    assert ultimo["months"][mes] > 0
    assert ultimo["bench"][mes] == pytest.approx(0.0)
    total = 1.0
    for y in d["years"]:
        total *= 1 + y["total"]
    assert total - 1 == pytest.approx((100 + 79) / 100 - 1)


def test_ruta_mensual_sin_movimientos(libro):
    d = libro.get("/api/cartera/mensual").get_json()
    assert d["years"] == [] and d["empty"] is True
