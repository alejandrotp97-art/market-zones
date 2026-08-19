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


# ── temporalidades: recortar no basta, hay que resembrar ──────────────────
def _libro_largo(cli, monkeypatch, n=800):
    serie = _serie("2023-01-02", [100 * (1.0008 ** i) for i in range(n)])
    monkeypatch.setattr(D, "_close_series", lambda t: serie)
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    _post(cli, ticker="AAA", side="buy", quantity=10, price=100, date="2023-01-02")
    return serie


def test_una_ventana_resiembra_el_indice_en_su_primer_dia(libro, monkeypatch):
    """Recortar el array sin más compararía tres meses de cartera contra una
    posición del índice sembrada hace dos años: la diferencia visible sería
    casi toda historia vieja arrastrada, no lo que ha pasado en el tramo."""
    _libro_largo(libro, monkeypatch)

    h = D._cartera_history("AAA", rango="3m")

    assert h["rebased"] is True
    # Las tres líneas parten del MISMO punto el primer día del tramo.
    assert h["portfolio"][0] == pytest.approx(h["invested"][0], abs=0.02)
    assert h["portfolio"][0] == pytest.approx(h["benchmark"][0], abs=0.02)


def test_sin_ventana_no_se_resiembra_nada(libro, monkeypatch):
    """OJO al montar el caso: el PRIMER día invertido y cartera coinciden por
    definición si se compró al precio de cierre. Lo que distingue una serie sin
    resembrar es que «invertido» se queda plano en el capital aportado mientras
    la cartera se mueve con el mercado."""
    _libro_largo(libro, monkeypatch)
    h = D._cartera_history("AAA")
    assert h["rebased"] is False
    assert h["invested"][-1] == pytest.approx(h["invested"][0], abs=0.02)
    assert h["portfolio"][-1] > h["invested"][-1] * 1.1


def test_el_tramo_empieza_en_el_cierre_ANTERIOR(libro, monkeypatch):
    """Para medir lo que hizo enero hace falta el cierre del 31 de diciembre.
    Sin él, el primer día del tramo no tiene contra qué medirse y su
    rendimiento se pierde — y «1 año» abarcaría 364 días, justo por debajo del
    umbral a partir del cual el panel anualiza."""
    _libro_largo(libro, monkeypatch)
    r = D._cartera_returns("AAA", rango="1y")
    assert r["twr"]["days"] >= 365
    assert r["annualizable"] is True


def test_en_una_ventana_corta_no_se_anualiza(libro, monkeypatch):
    _libro_largo(libro, monkeypatch)
    r = D._cartera_returns("AAA", rango="3m")
    assert r["annualizable"] is False
    assert r["twr"]["annualized"] is None


def test_la_tir_de_una_ventana_cuenta_el_capital_que_YA_habia(libro, monkeypatch):
    """En un tramo, el capital del primer día se trata como una compra de ese
    día: es lo que costó tener la cartera al abrirlo. Sin eso habría cobros sin
    ninguna salida que los pagara y la TIR se dispararía."""
    _libro_largo(libro, monkeypatch)
    r = D._cartera_returns("AAA", rango="6m")
    assert r["tir"] is not None
    assert -0.9 < r["tir"] < 5.0                 # una tasa, no un disparate


def test_ytd_arranca_en_el_cierre_del_ano_anterior(libro, monkeypatch):
    _libro_largo(libro, monkeypatch)
    r = D._cartera_returns("AAA", rango="ytd")
    assert r["from"][:4] == str(int(r["to"][:4]) - 1)


def test_una_ventana_sin_dos_puntos_cae_a_todo(libro, monkeypatch):
    """Una fecha fuera de rango no puede dejar el gráfico en blanco."""
    _libro_largo(libro, monkeypatch)
    h = D._cartera_history("AAA", desde="2099-01-01")
    assert h["range"] == "all" and len(h["dates"]) > 2


# ── caída máxima ──────────────────────────────────────────────────────────
def test_la_caida_se_mide_sobre_el_rendimiento_y_no_sobre_el_saldo(libro, monkeypatch):
    """Una aportación sube el saldo y no recupera nada. Si la caída saliera de
    los euros, la transferencia la daría por superada."""
    serie = _serie("2024-01-01", [100, 100, 70, 70, 70])
    monkeypatch.setattr(D, "_close_series", lambda t: serie)
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="buy", quantity=10, price=70, date="2024-01-04")

    r = D._cartera_returns("AAA")

    assert r["drawdown"]["max"] == pytest.approx(-0.30, abs=1e-6)
    assert r["drawdown"]["recovered"] is None
    assert r["drawdown"]["at_high"] is False


# ── rebalanceo ────────────────────────────────────────────────────────────
def test_la_aportacion_va_a_lo_que_esta_por_debajo(libro):
    _post(libro, ticker="AAA", side="buy", quantity=80, price=100)
    _post(libro, ticker="BBB", side="buy", quantity=20, price=100)
    for tk, tgt in (("AAA", 50), ("BBB", 50)):
        libro.post("/api/cartera/objetivo", json={"ticker": tk, "target": tgt},
                   headers={D.CSRF_HEADER: "1"})

    d = libro.get("/api/cartera/rebalanceo?cash=1000").get_json()

    assert d["buys"] == {"BBB": 1000.0}
    fila = {r["ticker"]: r for r in d["rows"]}
    assert fila["AAA"]["drift_pp"] > 0 and fila["BBB"]["drift_pp"] < 0


def test_una_posicion_sin_objetivo_no_se_cuenta_como_cero(libro):
    """Que nadie haya decidido su peso no significa que deba desaparecer de la
    cartera. Se queda fuera del reparto y se dice cuál es."""
    _post(libro, ticker="AAA", side="buy", quantity=50, price=100)
    _post(libro, ticker="BBB", side="buy", quantity=50, price=100)
    libro.post("/api/cartera/objetivo", json={"ticker": "AAA", "target": 100},
               headers={D.CSRF_HEADER: "1"})

    d = libro.get("/api/cartera/rebalanceo?cash=500").get_json()

    assert d["untargeted"] == ["BBB"]
    assert "BBB" not in d["buys"]


def test_un_objetivo_fuera_de_rango_se_rechaza(libro):
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    r = libro.post("/api/cartera/objetivo", json={"ticker": "AAA", "target": 140},
                   headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 400


# ── quién ha puesto el dinero ─────────────────────────────────────────────
def test_la_contribucion_suma_las_tres_piezas_de_cada_posicion(libro):
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="div", price=25, date="2024-02-01")
    p = _post(libro, ticker="AAA", side="sell", quantity=4, price=110, date="2024-03-01")

    pos = p["positions"][0]
    assert pos["contribution"] == pytest.approx(
        pos["unreal"] + pos["realized"] + pos["income"], abs=0.01)


# ── estado ejecutivo y cobertura ──────────────────────────────────────────
def test_la_cobertura_de_zona_es_NONE_con_la_cache_fria(libro, monkeypatch):
    """0% diría «ninguna posición tiene zona». La verdad con la caché vacía es
    «todavía no lo sé», y son dos afirmaciones distintas."""
    monkeypatch.setattr(D, "_close_series", lambda t: _serie("2024-01-01", [100] * 300))
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    monkeypatch.setattr(D, "_zone_cache", {})
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")

    e = D._cartera_estado()

    assert e["coverage"]["zona"] is None
    assert e["coverage"]["analisis"] == 100.0


def test_el_capital_aportado_no_es_el_coste_de_lo_abierto(libro, monkeypatch):
    """`invested` es el coste de lo que sigue abierto: una posición cerrada con
    beneficio desaparece de ahí como si nunca se hubiera aportado. Lo aportado
    es lo desplegado menos lo retirado."""
    monkeypatch.setattr(D, "_close_series", lambda t: _serie("2024-01-01", [100] * 300))
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="sell", quantity=4, price=100, date="2024-02-01")

    e = D._cartera_estado()

    assert e["contributed"] == pytest.approx(600.0, abs=0.5)   # 1000 puestos - 400 sacados


def test_una_cartera_sin_posiciones_no_finge_un_estado(libro):
    e = D._cartera_estado()
    assert e["n_positions"] == 0
    assert e["value"] == 0.0


# ── el plan de la persona ─────────────────────────────────────────────────
def test_un_campo_vacio_del_plan_borra_en_vez_de_poner_cero(libro):
    """No haber decidido un objetivo y haberse puesto uno de cero euros no son
    lo mismo. Se guarda NULL, y el progreso correspondiente desaparece."""
    libro.post("/api/cartera/plan", json={"capital": 50000, "monthly": 300},
               headers={D.CSRF_HEADER: "1"})
    assert D._portfolio_goal()["capital"] == 50000

    libro.post("/api/cartera/plan", json={"capital": ""}, headers={D.CSRF_HEADER: "1"})
    g = D._portfolio_goal()
    assert g["capital"] is None and g["monthly"] == 300


def test_un_plan_entero_vacio_se_borra(libro):
    libro.post("/api/cartera/plan", json={"capital": 1000}, headers={D.CSRF_HEADER: "1"})
    libro.post("/api/cartera/plan", json={"capital": "", "monthly": "", "horizon_years": ""},
               headers={D.CSRF_HEADER: "1"})
    assert D._portfolio_goal() is None


def test_un_objetivo_disparatado_se_rechaza(libro):
    r = libro.post("/api/cartera/plan", json={"horizon_years": 500},
                   headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 400


# ── aportaciones ──────────────────────────────────────────────────────────
def test_el_calendario_sale_de_la_misma_reconstruccion_que_la_rentabilidad(libro, monkeypatch):
    """Si saliera de otro sitio podría discrepar con la TIR, y entonces dos
    pantallas del mismo panel dirían cosas distintas del mismo dinero."""
    monkeypatch.setattr(D, "_close_series", lambda t: _serie("2024-01-01", [100] * 300))
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="buy", quantity=5, price=100, date="2024-03-01")

    a = D._cartera_aportaciones()
    r = D._cartera_returns("AAA")

    assert a["stats"]["total_in"] == pytest.approx(r["flows"]["aportado"], abs=0.01)
    assert [x["month"] for x in a["rows"]][:3] == ["2024-01", "2024-02", "2024-03"]
    assert a["rows"][1]["in"] == 0.0        # febrero vacío, pero presente


# ── simulador de venta ────────────────────────────────────────────────────
def test_pedir_mas_titulos_de_los_que_hay_se_REPORTA(libro):
    """Recortar la petición en silencio contestaría a una pregunta distinta de
    la que se hizo. Pedir 999 cuando hay 10 tiene que verse."""
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    d = libro.get("/api/cartera/simular-venta?ticker=AAA&qty=999").get_json()
    assert d["qty"] == 10 and d["short"] == 989


def test_las_plusvalias_del_ano_se_calculan_del_libro_y_mueven_el_tramo(libro):
    """Se obtienen corriendo la misma contabilidad dos veces y restando, no con
    un FIFO «por año» reimplementado: dos copias de esa regla divergen."""
    import datetime as dt
    ano = dt.date.today().year
    _post(libro, ticker="AAA", side="buy", quantity=100, price=10, date=f"{ano - 2}-01-01")
    _post(libro, ticker="AAA", side="sell", quantity=50, price=110, date=f"{ano}-02-01")

    d = libro.get("/api/cartera/simular-venta?ticker=AAA&qty=10").get_json()

    assert d["other_gains_auto"] is True
    assert d["other_gains"] == pytest.approx(5000.0, abs=1.0)   # 50 x (110-10)


def test_una_posicion_que_no_se_puede_valorar_no_se_simula(libro, monkeypatch):
    monkeypatch.setattr(D, "_last_price", lambda t: None)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    r = libro.get("/api/cartera/simular-venta?ticker=AAA&qty=1")
    assert r.status_code == 422


def test_un_instrumento_que_no_esta_en_cartera_da_404(libro):
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    assert libro.get("/api/cartera/simular-venta?ticker=ZZZ&qty=1").status_code == 404


# ── splits ────────────────────────────────────────────────────────────────
def test_un_split_posterior_a_una_compra_se_detecta_y_se_dice_que_cambiaria(libro, monkeypatch):
    monkeypatch.setattr(D, "_splits_of", lambda t: [{"date": "2024-06-10", "ratio": 10.0}])
    _post(libro, ticker="AAA", side="buy", quantity=10, price=400, date="2023-05-01")

    d = D._cartera_splits()

    assert d["n"] == 1
    s = d["pending"][0]
    assert s["ratio"] == 10.0 and s["qty_now"] == 10 and s["qty_if_applied"] == 100
    assert s["cost_ok"] is True


def test_aplicar_un_split_no_mueve_el_coste(libro, monkeypatch):
    """Es la condición que hace segura la operación más delicada del panel:
    reescribir movimientos ya apuntados."""
    monkeypatch.setattr(D, "_splits_of", lambda t: [{"date": "2024-06-10", "ratio": 10.0}])
    _post(libro, ticker="AAA", side="buy", quantity=10, price=400, date="2023-05-01")
    antes = libro.get("/api/cartera").get_json()["positions"][0]["invested"]

    r = libro.post("/api/cartera/splits",
                   json={"ticker": "AAA", "date": "2024-06-10", "action": "apply"},
                   headers={D.CSRF_HEADER: "1"})
    p = r.get_json()

    assert r.status_code == 200
    assert p["positions"][0]["qty"] == 100
    assert p["positions"][0]["invested"] == pytest.approx(antes, abs=0.01)
    assert p["split_applied"]["n"] == 1


def test_un_split_aplicado_deja_de_avisar(libro, monkeypatch):
    monkeypatch.setattr(D, "_splits_of", lambda t: [{"date": "2024-06-10", "ratio": 10.0}])
    _post(libro, ticker="AAA", side="buy", quantity=10, price=400, date="2023-05-01")
    libro.post("/api/cartera/splits",
               json={"ticker": "AAA", "date": "2024-06-10", "action": "apply"},
               headers={D.CSRF_HEADER: "1"})
    assert D._cartera_splits()["n"] == 0


def test_marcarlo_como_ya_tenido_en_cuenta_no_toca_el_libro(libro, monkeypatch):
    """El programa NO puede saber si la cantidad ya está en la escala nueva:
    «10 títulos» es el mismo número a los dos lados. Marcarlo sólo silencia."""
    monkeypatch.setattr(D, "_splits_of", lambda t: [{"date": "2024-06-10", "ratio": 10.0}])
    _post(libro, ticker="AAA", side="buy", quantity=10, price=400, date="2023-05-01")

    libro.post("/api/cartera/splits",
               json={"ticker": "AAA", "date": "2024-06-10", "action": "ack"},
               headers={D.CSRF_HEADER: "1"})

    assert D._cartera_splits()["n"] == 0
    assert libro.get("/api/cartera").get_json()["positions"][0]["qty"] == 10


def test_un_split_que_no_consta_en_la_fuente_no_se_aplica(libro, monkeypatch):
    monkeypatch.setattr(D, "_splits_of", lambda t: [])
    _post(libro, ticker="AAA", side="buy", quantity=10, price=400, date="2023-05-01")
    r = libro.post("/api/cartera/splits",
                   json={"ticker": "AAA", "date": "2024-06-10", "action": "apply"},
                   headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 404


def test_un_instrumento_sin_consultar_no_se_da_por_limpio(libro, monkeypatch):
    """`None` es «no lo he mirado» y `[]` es «no ha habido ninguno». Devolver
    lista vacía en los dos casos haría parecer limpio lo que no se ha visto."""
    monkeypatch.setattr(D, "_splits_of", lambda t: None)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=400, date="2023-05-01")
    d = D._cartera_splits()
    assert d["unchecked"] == ["AAA"] and d["n"] == 0
