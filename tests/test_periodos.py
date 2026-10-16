"""Rentabilidad por periodo de cada posición: 1D / 1S / 1M / YTD / 1A / Todo.

La fórmula (`cartera/periods.py`) y su cableado (`/api/cartera/periodos`). Lo
que se protege: que una compra dentro del tramo no cuente como ganancia, que una
posición abierta a mitad de tramo arranque en su compra, y que el dividendo sume.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D
from cartera import periods


def _idx(inicio, n):
    return pd.bdate_range(inicio, periods=n)


def _pos(valores, flujos=None, divs=None):
    n = len(valores)
    return {"value": np.array(valores, float),
            "flows": np.array(flujos or [0.0] * n, float),
            "divs": np.array(divs or [0.0] * n, float)}


# ── la fórmula ────────────────────────────────────────────────────────────
def test_1d_es_el_ultimo_cierre_contra_el_anterior():
    idx = _idx("2024-03-01", 5)
    r = periods.period_returns(idx, {"A": _pos([1000, 1000, 1000, 1000, 1100],
                                              [1000, 0, 0, 0, 0])})
    a = r["positions"]["A"]["1d"]
    assert a["abs"] == pytest.approx(100.0)
    assert a["pct"] == pytest.approx(0.10)


def test_una_compra_dentro_del_tramo_no_es_ganancia():
    """Comprar 1000 € más sube el valor 1000 € sin haber ganado nada."""
    idx = _idx("2024-03-01", 5)
    r = periods.period_returns(idx, {"A": _pos([1000, 1000, 1000, 2000, 2000],
                                              [1000, 0, 0, 1000, 0])})
    w = r["positions"]["A"]["1w"]
    assert w["abs"] == pytest.approx(0.0)
    assert w["pct"] == pytest.approx(0.0)


def test_posicion_abierta_dentro_del_tramo_arranca_en_su_compra():
    idx = _idx("2024-01-01", 300)
    vals = [0.0] * 290 + [500.0] * 9 + [550.0]
    flows = [0.0] * 290 + [500.0] + [0.0] * 9
    r = periods.period_returns(idx, {"A": _pos(vals, flows)})
    y = r["positions"]["A"]["1y"]
    assert y["abs"] == pytest.approx(50.0)
    assert y["pct"] == pytest.approx(0.10)
    assert r["positions"]["A"]["all"]["abs"] == pytest.approx(50.0)


def test_el_dividendo_cobrado_en_el_tramo_suma():
    idx = _idx("2024-03-01", 5)
    r = periods.period_returns(idx, {"A": _pos([1000] * 5, [1000, 0, 0, 0, 0],
                                              [0, 0, 20, 0, 0])})
    assert r["positions"]["A"]["1w"]["abs"] == pytest.approx(20.0)
    assert r["positions"]["A"]["all"]["pct"] == pytest.approx(0.02)


def test_ytd_mide_desde_el_ultimo_cierre_del_anyo_anterior():
    idx = _idx("2023-12-25", 10)          # 25-29 dic, 1-5 ene
    vals = [1000, 1000, 1000, 1000, 1000, 1200, 1200, 1200, 1200, 1250]
    r = periods.period_returns(idx, {"A": _pos(vals, [1000] + [0] * 9)})
    assert r["positions"]["A"]["ytd"]["abs"] == pytest.approx(250.0)
    assert r["positions"]["A"]["ytd"]["pct"] == pytest.approx(0.25)


def test_venta_dentro_del_tramo_cuenta_lo_cobrado():
    """Vender 500 € no es perder 500 €: el valor baja y el flujo es negativo."""
    idx = _idx("2024-03-01", 5)
    r = periods.period_returns(idx, {"A": _pos([1000, 1000, 500, 500, 500],
                                              [1000, 0, -500, 0, 0])})
    assert r["positions"]["A"]["1w"]["abs"] == pytest.approx(0.0)


def test_el_total_suma_euros_y_pondera_el_porcentaje():
    idx = _idx("2024-03-01", 5)
    r = periods.period_returns(idx, {
        "A": _pos([1000, 1000, 1000, 1000, 1100], [1000, 0, 0, 0, 0]),
        "B": _pos([3000, 3000, 3000, 3000, 2900], [3000, 0, 0, 0, 0])})
    t = r["total"]["1d"]
    assert t["abs"] == pytest.approx(0.0)
    assert t["pct"] == pytest.approx(0.0)
    assert r["windows"] == ["1d", "1w", "1m", "ytd", "1y", "all"]


def test_una_posicion_ya_vendida_no_aparece():
    idx = _idx("2024-03-01", 5)
    r = periods.period_returns(idx, {"A": _pos([1000, 0, 0, 0, 0],
                                              [1000, -1000, 0, 0, 0])})
    assert "A" not in r["positions"]


def test_sin_capital_expuesto_el_porcentaje_es_none():
    idx = _idx("2024-03-01", 3)
    r = periods.period_returns(idx, {"A": _pos([0, 0, 100], [0, 0, 0])})
    assert r["positions"]["A"]["1d"]["pct"] is None


# ── el cableado ───────────────────────────────────────────────────────────
@pytest.fixture
def libro(tmp_path, monkeypatch):
    db = str(tmp_path / "cartera.db")
    monkeypatch.setattr(D, "CARTERA_DB", db)
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


def test_endpoint_devuelve_cada_posicion_con_sus_seis_tramos(libro, monkeypatch):
    end = pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=30)
    precios = pd.Series(np.linspace(100, 129, 30), index=idx)
    monkeypatch.setattr(D, "_close_series", lambda t: precios)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100,
          date=idx[0].date().isoformat(), name="Acme")

    r = libro.get("/api/cartera/periodos")

    assert r.status_code == 200
    d = r.get_json()
    assert d["windows"] == ["1d", "1w", "1m", "ytd", "1y", "all"]
    fila = d["positions"][0]
    assert fila["ticker"] == "AAA" and fila["name"] == "Acme"
    assert fila["values"]["1d"]["abs"] == pytest.approx(10.0)
    assert fila["values"]["all"]["abs"] == pytest.approx(290.0)
    assert fila["values"]["all"]["pct"] == pytest.approx(0.29)
    assert d["total"]["all"]["abs"] == pytest.approx(290.0)


def test_endpoint_sin_movimientos_devuelve_vacio(libro):
    d = libro.get("/api/cartera/periodos").get_json()
    assert d["positions"] == [] and d["empty"] is True


def test_la_reconstruccion_sigue_sumando_lo_mismo(libro, monkeypatch):
    """Guardar la serie de cada posición no puede cambiar la suma."""
    end = pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=10)
    monkeypatch.setattr(D, "_close_series", lambda t: pd.Series([100.0] * 10, index=idx))
    _post(libro, ticker="AAA", side="buy", quantity=1, price=100, date=idx[0].date().isoformat())
    _post(libro, ticker="BBB", side="buy", quantity=2, price=100, date=idx[0].date().isoformat())
    r = D._reconstruct_portfolio("AAA")
    suma = sum(p["value"] for p in r["pos"].values())
    assert np.allclose(suma, r["port"])
    assert float(r["port"][-1]) == pytest.approx(300.0)
