"""El sembrador lee de la ficha de justETF también sectores y principales posiciones.

Sin red: la ficha y la respuesta del «Show more» de sectores están guardadas en
`tests/fixtures/` tal y como las sirvió justETF (iShares Core MSCI World, ficha
pública).
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "analysis"))
import seed_country_weights as S

FIX = os.path.join(HERE, "fixtures")


def _fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


PAGE = _fx("justetf_profile_holdings.html")
SECT_MORE = _fx("justetf_sectors_more.xml")


def test_principales_posiciones_con_nombre_isin_y_peso():
    h = S.parse_holdings(PAGE)
    assert len(h) == 10
    assert h[0] == {"name": "NVIDIA Corp.", "isin": "US67066G1040", "pct": 5.53}
    assert h[1]["isin"] == "US0378331005"
    assert sum(x["pct"] for x in h) == pytest.approx(26.64, abs=0.02)


def test_sectores_de_la_ficha_y_del_desplegable():
    assert S.parse_sectors(PAGE)[0] == ("Technology", 35.71)
    filas = S.parse_sectors(SECT_MORE)
    assert len(filas) == 14
    assert filas[-1] == ("Other", 0.18)


def test_pesos_de_sector_separan_el_resto_y_validan_la_suma():
    w, other = S.to_sector_weights(S.parse_sectors(SECT_MORE))
    assert w["Technology"] == 35.71
    assert "Other" not in w
    assert other == 0.18


def test_pesos_de_sector_que_no_suman_fallan():
    with pytest.raises(S.SeedError):
        S.to_sector_weights([("Technology", 30.0)])


def _fake_get(urls):
    def get(url, extra=None):
        urls.append(url)
        if "loadMoreSectors" in url:
            return SECT_MORE
        if "loadMoreCountries" in url:
            return PAGE              # sin expansión: se quedan los de la ficha
        return PAGE
    return get


def test_la_ficha_trae_paises_sectores_y_posiciones_siguiendo_cada_desplegable_UNA_vez():
    urls = []
    prof = S.fetch_profile("IE00B4L5Y983", get=_fake_get(urls))
    assert prof["countries"][0] == ("United States", 70.32)
    assert len(prof["sectors"]) == 14
    assert len(prof["holdings"]) == 10
    assert "iShares Core MSCI World" in prof["title"]
    assert sum("loadMoreSectors" in u for u in urls) == 1
    assert sum("loadMoreCountries" in u for u in urls) == 1


def test_fetch_countries_sigue_devolviendo_lo_de_siempre(monkeypatch):
    monkeypatch.setattr(S, "fetch_profile", lambda isin, get=None: {
        "countries": [("United States", 70.0)], "sectors": [], "holdings": [],
        "title": "X"})
    assert S.fetch_countries("IE00B4L5Y983") == ([("United States", 70.0)], "X")


def test_seed_escribe_sectores_y_posiciones_junto_a_los_paises(tmp_path, monkeypatch):
    out = tmp_path / "country_weights.json"
    monkeypatch.setattr(S, "OUT", str(out))
    monkeypatch.setattr(S, "holdings", lambda: [{"ticker": "IE00B4L5Y983.SG", "name": "World"}])
    real = S.fetch_profile
    monkeypatch.setattr(S, "fetch_profile", lambda isin, get=None: real(isin, get=_fake_get([])))
    assert S.seed(quiet=True) == 0
    inst = json.loads(out.read_text())["instruments"]["IE00B4L5Y983.SG"]
    assert inst["weights"]["US"] == 70.32
    assert inst["sectors"]["Technology"] == 35.71
    assert inst["sectors_other"] == 0.18
    assert inst["holdings"][0]["isin"] == "US67066G1040"
    assert inst["holdings_count"] == "1,252"


def test_un_fallo_en_sectores_no_tira_los_paises(tmp_path, monkeypatch):
    """Los sectores son un añadido: si su tabla viene rara, el país —que el mapa
    ya usa— se escribe igual y el sector queda vacío y dicho."""
    out = tmp_path / "country_weights.json"
    monkeypatch.setattr(S, "OUT", str(out))
    monkeypatch.setattr(S, "holdings", lambda: [{"ticker": "IE00B4L5Y983.SG", "name": "World"}])
    monkeypatch.setattr(S, "fetch_profile", lambda isin, get=None: {
        "countries": [("United States", 70.32), ("Other", 29.68)],
        "sectors": [("Technology", 10.0)], "holdings": [], "title": "W",
        "holdings_count": None})
    assert S.seed(quiet=True) == 0
    inst = json.loads(out.read_text())["instruments"]["IE00B4L5Y983.SG"]
    assert inst["weights"] == {"US": 70.32}
    assert inst["sectors"] == {} and inst["sectors_error"]
