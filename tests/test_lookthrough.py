"""Sectores y X-ray: los fondos abiertos en lo que tienen dentro.

La fórmula (`cartera/lookthrough.py`) y sus dos rutas. La regla que se protege
es la misma que la del mapa: sólo se enseña la exposición que la tabla respalda;
lo que no se puede colocar sale como su propia cifra, nunca repartido.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D
from cartera import lookthrough as L

TABLE = {"instruments": {
    "WORLD": {"asset_class": "equity", "weights": {"US": 70.0}, "other": 30.0,
              "sectors": {"Technology": 30.0, "Finance": 20.0}, "sectors_other": 50.0,
              "holdings": [{"name": "NVIDIA Corp.", "isin": "US67066G1040", "pct": 5.0},
                           {"name": "Apple", "isin": "US0378331005", "pct": 4.0}]},
    "SP500": {"asset_class": "equity", "weights": {"US": 100.0}, "other": 0.0,
              "sectors": {"Technology": 40.0, "Healthcare": 60.0}, "sectors_other": 0.0,
              "holdings": [{"name": "NVIDIA", "isin": "US67066G1040", "pct": 7.0},
                           {"name": "Microsoft", "isin": "US5949181045", "pct": 6.0}]},
    "GOLD": {"asset_class": "commodity", "weights": {}, "other": 0.0, "no_geography": True},
    "OLD": {"asset_class": "equity", "weights": {"US": 100.0}, "other": 0.0},
}}


def _p(t, mv, kind="ETF", name=None, valued=True):
    return {"ticker": t, "name": name or t, "market_value": mv, "valued": valued,
            "kind": kind, "qty": 1.0}


# ── sectores ──────────────────────────────────────────────────────────────
def test_cada_fondo_reparte_su_valor_entre_sus_sectores():
    r = L.sector_exposure([_p("WORLD", 1000), _p("SP500", 1000)], TABLE)
    tech = next(s for s in r["sectors"] if s["key"] == "Technology")
    assert tech["eur"] == pytest.approx(300 + 400)
    assert tech["name"] == "Tecnología"
    assert r["mapped_eur"] == pytest.approx(300 + 200 + 400 + 600)
    assert tech["pct"] == pytest.approx(700 / 1500 * 100)


def test_el_resto_sin_nombre_no_se_reparte():
    r = L.sector_exposure([_p("WORLD", 1000)], TABLE)
    assert r["other_eur"] == pytest.approx(500)
    assert sum(s["eur"] for s in r["sectors"]) == pytest.approx(500)


def test_oro_y_fondo_sin_sectores_salen_aparte_con_motivo():
    r = L.sector_exposure([_p("GOLD", 200), _p("OLD", 300), _p("NADA", 50)], TABLE)
    why = {o["ticker"]: o["why"] for o in r["outside"]}
    assert why["GOLD"] == "no tiene sector"
    assert why["OLD"] == "sin sectores todavía"
    assert why["NADA"] == "sin ficha del fondo"
    assert r["outside_eur"] == pytest.approx(550)
    assert r["sectors"] == []


def test_sin_valorar_va_a_excluidos():
    r = L.sector_exposure([_p("WORLD", None, valued=False)], TABLE)
    assert r["excluded"][0]["ticker"] == "WORLD"


# ── X-ray ─────────────────────────────────────────────────────────────────
def test_una_accion_en_dos_fondos_se_suma_y_dice_por_donde_llega():
    r = L.xray([_p("WORLD", 1000), _p("SP500", 2000)], TABLE)
    nv = next(h for h in r["holdings"] if h["isin"] == "US67066G1040")
    assert nv["eur"] == pytest.approx(50 + 140)
    assert {v["ticker"] for v in nv["via"]} == {"WORLD", "SP500"}
    assert [h["isin"] for h in r["shared"]] == ["US67066G1040"]
    assert r["holdings"][0]["isin"] == "US67066G1040"          # ordenado por euros


def test_la_accion_que_tienes_directa_suma_con_la_del_fondo():
    r = L.xray([_p("WORLD", 1000), _p("US0378331005.DE", 100, kind="Acción", name="Apple")],
               TABLE)
    ap = next(h for h in r["holdings"] if h["isin"] == "US0378331005")
    assert ap["eur"] == pytest.approx(40 + 100)
    assert any(v["ticker"] == "US0378331005.DE" and v["direct"] for v in ap["via"])
    assert ap in r["shared"]


def test_solape_entre_fondos_es_la_suma_de_los_minimos():
    r = L.xray([_p("WORLD", 1000), _p("SP500", 1000)], TABLE)
    par = r["pairs"][0]
    assert {par["a"], par["b"]} == {"WORLD", "SP500"}
    assert par["overlap_pct"] == pytest.approx(5.0)            # min(5, 7) de NVIDIA
    assert par["common"] == ["NVIDIA Corp."]


def test_fondo_sin_posiciones_se_dice_y_el_porcentaje_es_sobre_la_cartera():
    r = L.xray([_p("WORLD", 1000), _p("OLD", 1000)], TABLE)
    assert [w["ticker"] for w in r["without"]] == ["OLD"]
    nv = next(h for h in r["holdings"] if h["isin"] == "US67066G1040")
    assert nv["pct"] == pytest.approx(50 / 2000 * 100)
    assert r["portfolio_eur"] == pytest.approx(2000)
    assert r["seen_eur"] == pytest.approx(90)


# ── las rutas ─────────────────────────────────────────────────────────────
@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(D, "PUBLIC_HOST", "")
    monkeypatch.setattr(D.geo, "load_table", lambda *a, **k: TABLE)
    return D.app.test_client()


def test_ruta_sectores(cli, monkeypatch):
    monkeypatch.setattr(D, "_cartera_payload", lambda: {
        "positions": [_p("WORLD", 1000), _p("SP500", 1000)],
        "summary": {"market_value": 2000.0}, "movements": []})
    d = cli.get("/api/cartera/sectores").get_json()
    assert d["sectors"][0]["key"] == "Technology"
    assert d["portfolio_eur"] == 2000.0
    assert "seeding" in d


def test_ruta_xray(cli, monkeypatch):
    monkeypatch.setattr(D, "_cartera_payload", lambda: {
        "positions": [_p("WORLD", 1000), _p("SP500", 1000)],
        "summary": {"market_value": 2000.0}, "movements": []})
    d = cli.get("/api/cartera/xray").get_json()
    assert d["shared"][0]["isin"] == "US67066G1040"
    assert d["pairs"][0]["overlap_pct"] == pytest.approx(5.0)


def test_rutas_ignoran_lo_ya_vendido(cli, monkeypatch):
    vendida = dict(_p("WORLD", 0), qty=0.0)
    monkeypatch.setattr(D, "_cartera_payload", lambda: {
        "positions": [vendida], "summary": {"market_value": 0.0}, "movements": []})
    assert cli.get("/api/cartera/sectores").get_json()["outside"] == []
    assert cli.get("/api/cartera/xray").get_json()["holdings"] == []


def test_los_sectores_de_renta_fija_tambien_se_traducen():
    assert L.sector_name("Sovereign") == "Deuda soberana"
    assert L.sector_name("Algo nuevo") == "Algo nuevo"
