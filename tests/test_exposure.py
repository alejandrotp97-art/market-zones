"""Exposición por divisa: dos lecturas que no dicen lo mismo.

La de cotización es exacta y poco informativa; la económica es aproximada y es
la que importa. Los tests fijan sobre todo lo que NO se hace: no repartir lo
desconocido entre lo conocido, y no meter países europeos en el euro por
cercanía geográfica.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from cartera.exposure import (EUROZONA, by_economic_currency,
                              by_quote_currency, moneda_de)


def pos(tk, ccy, mv, **kw):
    d = {"ticker": tk, "ccy": ccy, "market_value": mv, "qty": 1.0, "valued": True}
    d.update(kw)
    return d


# ── el mapa país -> moneda ────────────────────────────────────────────────
def test_un_pais_europeo_fuera_del_euro_NO_se_mete_en_el_euro():
    """Suiza, Reino Unido, Suecia o Polonia están en Europa y tienen su propia
    moneda. Meterlos en el saco del euro por cercanía sería justo el error que
    esta tabla existe para no cometer."""
    for c in ("CH", "GB", "SE", "PL", "NO", "DK", "CZ", "HU"):
        assert moneda_de(c) not in ("EUR", ""), c
    for c in ("DE", "ES", "FR", "IE", "HR"):
        assert moneda_de(c) == "EUR", c


def test_la_zona_euro_esta_enumerada_y_no_adivinada():
    assert len(EUROZONA) == 20
    assert "HR" in EUROZONA and "CH" not in EUROZONA


def test_un_pais_desconocido_no_se_inventa_moneda():
    assert moneda_de("XX") == "" and moneda_de(None) == "" and moneda_de("") == ""


# ── divisa de cotización ──────────────────────────────────────────────────
def test_el_reparto_por_cotizacion_es_exacto_y_suma_cien():
    r = by_quote_currency([pos("A", "USD", 6000), pos("B", "EUR", 4000)])
    assert {x["ccy"]: x["pct"] for x in r["rows"]} == {"USD": 60.0, "EUR": 40.0}
    assert r["total"] == 10000.0


def test_una_posicion_sin_valorar_no_cuenta_como_cero():
    """Contarla a cero bajaría el peso de su divisa sin que se note. Se excluye
    y se dice cuántas quedan fuera."""
    r = by_quote_currency([pos("A", "USD", 5000),
                           pos("B", "GBP", None, valued=False)])
    assert r["unvalued"] == 1
    assert [x["ccy"] for x in r["rows"]] == ["USD"]


# ── divisa económica ──────────────────────────────────────────────────────
def test_la_transparencia_convierte_paises_en_monedas():
    r = by_economic_currency([{"iso2": "US", "eur": 5500},
                              {"iso2": "DE", "eur": 2000},
                              {"iso2": "FR", "eur": 500},
                              {"iso2": "JP", "eur": 2000}], mapped_eur=10000)
    d = {x["ccy"]: x["eur"] for x in r["rows"]}
    assert d["USD"] == 5500 and d["EUR"] == 2500 and d["JPY"] == 2000
    assert r["coverage_pct"] == 100.0


def test_lo_que_no_se_mapea_NO_se_reparte_entre_lo_demas():
    """Repartirlo inflaría en proporción todas las divisas conocidas y haría
    parecer completa una foto que no lo está."""
    r = by_economic_currency([{"iso2": "US", "eur": 8000},
                              {"iso2": "XX", "eur": 2000}], mapped_eur=10000)
    assert r["unmapped"] == 2000.0
    assert r["unmapped_countries"] == ["XX"]
    assert [x["ccy"] for x in r["rows"]] == ["USD"]
    assert r["rows"][0]["pct"] == 100.0        # sobre lo MAPEADO, y se declara
    assert r["coverage_pct"] == 80.0           # ...que es el 80% de lo que llegó


def test_sin_paises_no_hay_reparto_economico():
    r = by_economic_currency([], mapped_eur=0)
    assert r["rows"] == [] and r["coverage_pct"] is None


def test_una_cartera_en_euros_puede_depender_del_dolar():
    """El caso que justifica la sección entera: por cotización sale 100% EUR y
    por transparencia más de la mitad en dólares."""
    q = by_quote_currency([pos("MUNDIAL", "EUR", 10000)])
    e = by_economic_currency([{"iso2": "US", "eur": 6500},
                              {"iso2": "JP", "eur": 1500},
                              {"iso2": "DE", "eur": 2000}], mapped_eur=10000)
    assert q["rows"][0]["ccy"] == "EUR" and q["rows"][0]["pct"] == 100.0
    assert e["rows"][0]["ccy"] == "USD" and e["rows"][0]["pct"] == 65.0
