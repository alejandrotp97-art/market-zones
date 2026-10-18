"""La foto del ciclo y su endpoint `/api/cartera/ciclo`.

Lo que se protege: que la foto lleve el tono de los titulares como un dato más
del pilar de noticias, que una fuente caída se diga y no tumbe la foto, que el
endpoint sirva la foto fresca sin recalcular, y que cuando el recálculo falla
sirva la vieja MARCADA como vieja — o un error claro si no hay ninguna.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D
from macro import build as B
from macro import web as W


def _cli():
    vals = [99.0, 99.4, 99.8, 100.2, 100.6, 100.8, 100.9, 101.0, 101.1, 101.2]
    return [(f"2025-{m:02d}-01", v) for m, v in zip(range(1, 11), vals, strict=True)]


NEWS = [{"title": "Recession fears grow", "source": "R", "link": "https://n/1", "date": "2025-12-30"},
        {"title": "Stocks rally to record high", "source": "C", "link": "https://n/2", "date": "2025-12-30"},
        {"title": "Autumn weather", "source": "L", "link": "https://n/3", "date": "2025-12-29"}]


def test_la_foto_mete_el_tono_como_dato_y_dice_que_fuentes_fallaron():
    data = {"CLI:USA": _cli(), "VIXCLS": [("2025-12-30", 15.0)]}
    status = [{"id": "VIXCLS", "ok": True, "stale": False}, {"id": "NFCI", "ok": False, "error": "timeout"}]
    snap = B.snapshot(data, NEWS, status, today="2025-12-31", now=1000.0)
    noticias = next(p for p in snap["pillars"] if p["key"] == "noticias")
    tono = next(i for i in noticias["indicators"] if i["key"] == "headlines")
    assert tono["value"] == pytest.approx(0.0)              # 2 negativas (recession, fears) / 2 positivas
    assert snap["news"]["n"] == 3 and len(snap["news"]["headlines"]) == 3
    assert snap["news"]["headlines"][0]["mood"] != 0        # primero los que tienen tono
    assert snap["sources"]["failed"] == ["NFCI"]
    assert snap["generated_at"] == 1000.0 and snap["phase"] == "auge"
    assert {p["key"] for p in snap["phases"]} == {"auge", "recesion", "crisis", "recuperacion"}
    json.dumps(snap)                                         # serializable tal cual


def test_sin_cli_no_hay_foto():
    with pytest.raises(B.BuildError, match="CLI"):
        B.snapshot({"VIXCLS": [("2025-12-30", 15.0)]}, [], [], today="2025-12-31", now=0)


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "SNAPSHOT_PATH", str(tmp_path / "ciclo.json"))
    return D.app.test_client()


def test_foto_fresca_se_sirve_sin_recalcular(cliente, monkeypatch):
    B.write_snapshot({"phase": "auge", "generated_at": W.time.time()})
    monkeypatch.setattr(B, "refresh", lambda: pytest.fail("no debía recalcular"))
    r = cliente.get("/api/cartera/ciclo")
    assert r.status_code == 200 and r.get_json()["phase"] == "auge"
    assert "stale" not in r.get_json()


def test_sin_foto_se_calcula_y_se_guarda(cliente, monkeypatch):
    monkeypatch.setattr(B, "refresh", lambda: B.write_snapshot({"phase": "crisis", "generated_at": 5.0}))
    r = cliente.get("/api/cartera/ciclo")
    assert r.status_code == 200 and r.get_json()["phase"] == "crisis"


def test_si_el_recalculo_falla_sirve_la_vieja_marcada(cliente, monkeypatch):
    B.write_snapshot({"phase": "auge", "generated_at": 1.0})

    def roto():
        raise B.BuildError("OCDE caída")

    monkeypatch.setattr(B, "refresh", roto)
    r = cliente.get("/api/cartera/ciclo")
    assert r.status_code == 200
    assert r.get_json()["phase"] == "auge" and r.get_json()["stale"] is True


def test_sin_foto_y_sin_recalculo_es_un_error_claro(cliente, monkeypatch):
    def roto():
        raise B.BuildError("OCDE caída")

    monkeypatch.setattr(B, "refresh", roto)
    r = cliente.get("/api/cartera/ciclo")
    assert r.status_code == 502 and "OCDE caída" in r.get_json()["error"]


def test_la_foto_es_semanal_tres_dias_valen_y_nueve_no(cliente, monkeypatch):
    # El timer la rehace los domingos: a mitad de semana la foto es la buena y
    # recalcularla al abrir la página haría inútil el calendario semanal.
    dia = 24 * 3600
    B.write_snapshot({"phase": "auge", "generated_at": W.time.time() - 3 * dia})
    monkeypatch.setattr(B, "refresh", lambda: pytest.fail("a los 3 días no se recalcula"))
    assert cliente.get("/api/cartera/ciclo").get_json()["phase"] == "auge"
    B.write_snapshot({"phase": "auge", "generated_at": W.time.time() - 9 * dia})
    monkeypatch.setattr(B, "refresh", lambda: B.write_snapshot({"phase": "crisis", "generated_at": 1.0}))
    assert cliente.get("/api/cartera/ciclo").get_json()["phase"] == "crisis"
