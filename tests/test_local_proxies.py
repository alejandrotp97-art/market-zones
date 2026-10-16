"""Las referencias de instrumentos REALES viven fuera del repositorio.

`data/local_proxies.json` (ignorado por git) añade, al arrancar, entradas a
`HISTORY_PROXY` del panel y a `PROXIES` del sembrador. Así el código publicado
no nombra ninguna posición de nadie y la instalación sigue sabiendo, por
ejemplo, de qué hermano tomar el histórico de un ETC que Yahoo no traza.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "analysis"))

import seed_country_weights as S

import dashboard as D

LOCAL = {
    "history_proxy": {"XX0000000001.SG": "HERMANO.MI"},
    "seed_proxies": {"XX0000000001.SG": {
        "asset_class": "commodity", "proxy_isin": None, "proxy_name": None,
        "proxy_note": "materia prima, sin país"}},
}


def _write(tmp_path, data):
    p = tmp_path / "local_proxies.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(p)


def test_el_panel_suma_los_proxies_locales(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "HISTORY_PROXY", dict(D.HISTORY_PROXY))
    n = D._apply_local_proxies(_write(tmp_path, LOCAL))
    assert n == 1
    assert D.HISTORY_PROXY["XX0000000001.SG"] == "HERMANO.MI"


def test_el_sembrador_suma_los_proxies_locales(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "PROXIES", dict(S.PROXIES))
    S.apply_local_proxies(_write(tmp_path, LOCAL))
    r = S.resolve("XX0000000001.SG", "ETC")
    assert r["asset_class"] == "commodity" and r["proxy_isin"] is None
    assert r["needs_proxy"] is False


def test_sin_fichero_no_cambia_nada(tmp_path, monkeypatch):
    antes = dict(D.HISTORY_PROXY)
    monkeypatch.setattr(D, "HISTORY_PROXY", dict(antes))
    assert D._apply_local_proxies(str(tmp_path / "no-existe.json")) == 0
    assert D.HISTORY_PROXY == antes
    monkeypatch.setattr(S, "PROXIES", dict(S.PROXIES))
    S.apply_local_proxies(str(tmp_path / "no-existe.json"))


def test_un_fichero_roto_no_tumba_el_arranque(tmp_path, monkeypatch, capsys):
    antes = dict(D.HISTORY_PROXY)
    monkeypatch.setattr(D, "HISTORY_PROXY", dict(antes))
    assert D._apply_local_proxies(_write(tmp_path, "{no es json")) == 0
    assert D.HISTORY_PROXY == antes
    assert "local_proxies" in capsys.readouterr().err


def test_entradas_con_forma_rara_se_ignoran(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "HISTORY_PROXY", dict(D.HISTORY_PROXY))
    n = D._apply_local_proxies(_write(tmp_path, {"history_proxy": {"A.SG": 5, "B.SG": "B.MI"}}))
    assert n == 1 and "A.SG" not in D.HISTORY_PROXY


def test_el_fichero_real_esta_ignorado_por_git():
    with open(os.path.join(os.path.dirname(HERE), ".gitignore"), encoding="utf-8") as f:
        assert "data/local_proxies.json" in f.read().split()
