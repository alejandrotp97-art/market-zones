"""Rentabilidad, costes, divisa y diversificación: el cableado del panel.

`tests/test_returns.py` cubre las fórmulas. Esto cubre lo que las rodea, que es
donde se pierden: qué serie se les pasa, con qué signo viaja cada flujo, y qué
se enseña cuando no hay bastante historia para decir nada.
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


@pytest.fixture
def libro(tmp_path, monkeypatch):
    db = str(tmp_path / "cartera.db")
    monkeypatch.setattr(D, "CARTERA_DB", db)
    monkeypatch.setattr(D, "_instrument_ccy", lambda s: "EUR")
    monkeypatch.setattr(D, "_fx_series_eur", lambda c: None)
    monkeypatch.setattr(D, "_fx_now", lambda c: 1.0)
    monkeypatch.setattr(D, "_last_price", lambda t: 120.0)
    monkeypatch.setattr(D, "_seed_geo_async", lambda *a, **k: None)
    monkeypatch.setattr(D, "PUBLIC_HOST", "")
    return D.app.test_client()


def _post(cli, **body):
    r = cli.post("/api/cartera", json=body, headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _serie(inicio, valores):
    idx = pd.bdate_range(inicio, periods=len(valores))
    return pd.Series([float(v) for v in valores], index=idx)


# ── el convenio de los flujos, que es donde se falla ──────────────────────
def test_el_dividendo_llega_al_calculo_como_RETIRADA(libro, monkeypatch):
    """Un reparto hace caer el precio sin que se haya perdido nada. Si entrase
    como cero, o peor, como aportación, el TWR restaría rentabilidad en cada
    cobro. Aquí se comprueba el SIGNO con el que sale de la reconstrucción."""
    monkeypatch.setattr(D, "_close_series", lambda t: _serie("2024-01-01", [100] * 10))
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="div", price=50, date="2024-01-04")

    r = D._reconstruct_portfolio("AAA")

    assert r["empty"] is False
    assert float(sum(r["divs"])) == pytest.approx(50.0, abs=1e-9)
    # y NO se ha colado en la compraventa: sólo está la compra de 1000
    assert float(sum(r["flows"])) == pytest.approx(1000.0, abs=1e-9)


def test_el_dividendo_de_un_activo_excluido_sale_con_el(libro, monkeypatch):
    """Si una posición se cae del gráfico por falta de serie, su reparto se cae
    con ella: si no, la rentabilidad tendría un ingreso sin el activo que lo
    generó, y saldría de la nada."""
    monkeypatch.setattr(D, "_close_series",
                        lambda t: _serie("2024-01-01", [100] * 10) if t == "AAA" else None)
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="BBB", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="BBB", side="div", price=50, date="2024-01-04")

    r = D._reconstruct_portfolio("AAA")

    assert "BBB" in r["excluded"]
    assert float(sum(r["divs"])) == 0.0


def test_la_rentabilidad_se_calcula_a_resolucion_DIARIA(libro, monkeypatch):
    """El gráfico submuestrea para pintar. Encadenar tramos de uno de cada
    cuatro días coloca los flujos en el día que no es, así que el cálculo tiene
    que ir por la reconstrucción cruda y no por el payload del gráfico."""
    monkeypatch.setattr(D, "_close_series", lambda t: _serie("2020-01-01", range(100, 1200)))
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    _post(libro, ticker="AAA", side="buy", quantity=1, price=100, date="2020-01-01")

    hist = D._cartera_history("AAA", max_points=50)
    rend = D._cartera_returns("AAA")

    assert len(hist["dates"]) <= 51                  # el gráfico va recortado
    assert rend["twr"]["periods"] > 500              # el cálculo, no


# ── qué se enseña cuando no hay bastante ──────────────────────────────────
def test_con_menos_de_un_ano_no_se_anualiza_nada(libro, monkeypatch):
    monkeypatch.setattr(D, "_close_series", lambda t: _serie("2026-06-01", [100 + i for i in range(40)]))
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    _post(libro, ticker="AAA", side="buy", quantity=1, price=100, date="2026-06-01")

    r = D._cartera_returns("AAA")

    assert r["annualizable"] is False
    assert r["twr"]["annualized"] is None
    assert r["twr"]["total"] is not None            # el acumulado sí se puede dar


def test_una_cartera_vacia_no_devuelve_un_cero(libro):
    r = D._cartera_returns("SPY")
    assert r["empty"] is True and r["twr"] is None


# ── costes ────────────────────────────────────────────────────────────────
def test_las_comisiones_se_suman_y_la_retencion_va_aparte(libro):
    """Son cosas distintas: la comisión es un precio que se negocia cambiando
    de bróker; la retención es un impuesto a cuenta que se recupera en parte."""
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, fee=2.5, date="2024-01-01")
    _post(libro, ticker="AAA", side="sell", quantity=2, price=110, fee=1.5, date="2024-02-01")
    p = _post(libro, ticker="AAA", side="div", price=30, fee=4.5, date="2024-03-01")

    s = p["summary"]
    assert s["fees"] == 4.0                          # 2,5 + 1,5, sin la retención
    assert s["withheld"] == 4.5
    assert s["n_ops"] == 2                           # el dividendo no es una operación


def test_el_ter_se_declara_y_produce_un_coste_anual(libro):
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    r = libro.post("/api/cartera/ter", json={"ticker": "AAA", "ter": 0.20},
                   headers={D.CSRF_HEADER: "1"})
    p = r.get_json()

    pos = p["positions"][0]
    assert pos["ter"] == 0.20
    assert pos["ter_year"] == round(pos["market_value"] * 0.002, 2)
    assert p["summary"]["ter_coverage"] == 100.0


def test_un_ter_del_12_por_ciento_se_rechaza(libro):
    """Casi siempre es un 0,12 tecleado como 12. Aceptarlo dispara el coste
    proyectado a veinte años y nadie entiende de dónde sale el número."""
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    r = libro.post("/api/cartera/ter", json={"ticker": "AAA", "ter": 12},
                   headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 400


def test_sin_ter_declarado_el_coste_no_se_da_por_cero(libro):
    """Un cero diría «esto no te cuesta nada». La verdad es «no lo sé», y la
    cobertura tiene que decir sobre qué parte del capital se está hablando."""
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    _post(libro, ticker="BBB", side="buy", quantity=10, price=100)
    libro.post("/api/cartera/ter", json={"ticker": "AAA", "ter": 0.20},
               headers={D.CSRF_HEADER: "1"})
    p = libro.get("/api/cartera").get_json()

    porticker = {x["ticker"]: x for x in p["positions"]}
    assert porticker["BBB"]["ter"] is None
    assert porticker["BBB"]["ter_year"] is None
    assert p["summary"]["ter_coverage"] == 50.0


def test_el_ter_sobrevive_a_vender_la_posicion(libro):
    """El folleto de un fondo no cambia porque tú lo vendas. Volver a teclearlo
    al recomprarlo sería trabajo repetido para un dato que no ha variado."""
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    libro.post("/api/cartera/ter", json={"ticker": "AAA", "ter": 0.20},
               headers={D.CSRF_HEADER: "1"})
    _post(libro, ticker="AAA", side="sell", quantity=10, price=110, date="2024-02-01")
    p = _post(libro, ticker="AAA", side="buy", quantity=5, price=115, date="2024-03-01")

    assert p["positions"][0]["ter"] == 0.20


# ── activo frente a divisa ────────────────────────────────────────────────
def test_el_reparto_activo_divisa_suma_el_no_realizado(libro, monkeypatch):
    monkeypatch.setattr(D, "_instrument_ccy", lambda s: "USD")
    monkeypatch.setattr(D, "_fx_now", lambda c: 0.80)
    monkeypatch.setattr(D, "_fx_series_eur", lambda c: _serie("2024-01-01", [0.90] * 5))
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")

    p = libro.get("/api/cartera").get_json()
    pos = p["positions"][0]

    assert pos["split"] is not None
    assert pos["split"]["asset"] + pos["split"]["currency"] == pytest.approx(pos["unreal"], abs=0.01)
    assert pos["split"]["currency"] < 0              # el dólar cayó de 0,90 a 0,80


def test_en_euros_no_hay_efecto_divisa(libro):
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    pos = libro.get("/api/cartera").get_json()["positions"][0]
    assert pos["split"]["currency"] == pytest.approx(0.0, abs=1e-9)


# ── diversificación ───────────────────────────────────────────────────────
def test_con_una_sola_posicion_no_hay_matriz(libro):
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    c = D._cartera_correlacion()
    assert c["matrix"] == [] and c["eff_n_corr"] == 1.0


def test_dos_activos_identicos_son_UNA_apuesta(libro, monkeypatch):
    """El caso que justifica toda la sección. Dos series idénticas correlacionan
    a 1, y la diversificación real es 1 aunque contando líneas sean 2."""
    s = _serie("2024-01-01", [100 * (1.001 ** i) * (1 + 0.01 * ((-1) ** i)) for i in range(300)])
    monkeypatch.setattr(D, "_close_series", lambda t: s)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    _post(libro, ticker="BBB", side="buy", quantity=10, price=100)

    c = D._cartera_correlacion()

    assert c["eff_n_weights"] == pytest.approx(2.0, abs=0.01)
    assert c["eff_n_corr"] == pytest.approx(1.0, abs=0.01)


def test_una_posicion_sin_serie_se_excluye_y_se_dice(libro, monkeypatch):
    s = _serie("2024-01-01", [100 + (i % 7) for i in range(300)])
    monkeypatch.setattr(D, "_close_series", lambda t: s if t == "AAA" else None)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    _post(libro, ticker="BBB", side="buy", quantity=10, price=100)

    c = D._cartera_correlacion()

    assert [x["ticker"] for x in c["excluded"]] == ["BBB"]


def test_con_pocas_sesiones_en_comun_no_se_publica_una_correlacion(libro, monkeypatch):
    """Estimar una correlación con veinte datos es publicar ruido con dos
    decimales. Se dice cuántas sesiones hay y no se da el número."""
    s = _serie("2024-01-01", [100 + (i % 5) for i in range(20)])
    monkeypatch.setattr(D, "_close_series", lambda t: s)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    _post(libro, ticker="BBB", side="buy", quantity=10, price=100)

    c = D._cartera_correlacion()

    assert c["eff_n_corr"] is None
    assert "sesiones en común" in c["why"]
