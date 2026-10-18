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
    monkeypatch.setattr(W, "_last_fail", 0.0)      # la espera tras un fallo no cruza tests
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
    # Claro para quien abre la página, y sin el detalle interno (va al journal).
    assert r.status_code == 502 and "No pude calcular el ciclo" in r.get_json()["error"]
    assert "OCDE caída" not in r.get_json()["error"]


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


def test_un_cli_caducado_tampoco_da_foto():
    # Existe pero no sirve (un solo dato, viejo): sin reloj no hay fase, y una
    # foto con fase vacía dejaba la tarjeta en blanco y tumbaba `build.main`.
    with pytest.raises(B.BuildError, match="CLI"):
        B.snapshot({"CLI:USA": [("2020-01-01", 100.5)]}, [], [], today="2026-10-18", now=0)


def test_el_502_no_ensena_el_error_interno(cliente, monkeypatch):
    def roto():
        raise PermissionError("[Errno 30] Read-only file system: '/srv/secreto/data/macro/ciclo.json'")

    monkeypatch.setattr(B, "refresh", roto)
    monkeypatch.setattr(W, "_last_fail", 0.0)
    r = cliente.get("/api/cartera/ciclo")
    assert r.status_code == 502
    assert "/srv/secreto" not in r.get_data(as_text=True)


def test_tras_un_fallo_no_se_reintenta_en_cada_carga(cliente, monkeypatch):
    llamadas = []

    def roto():
        llamadas.append(1)
        raise B.BuildError("OCDE caída")

    B.write_snapshot({"phase": "auge", "generated_at": 1.0})
    monkeypatch.setattr(B, "refresh", roto)
    monkeypatch.setattr(W, "_last_fail", 0.0)
    for _ in range(3):
        r = cliente.get("/api/cartera/ciclo")
        assert r.get_json()["stale"] is True
    assert len(llamadas) == 1                 # las otras dos sirven la vieja sin bajar nada


def test_una_foto_hecha_con_copias_viejas_se_reintenta_antes(cliente, monkeypatch):
    hace_7h = W.time.time() - 7 * 3600
    B.write_snapshot({"phase": "auge", "generated_at": hace_7h, "sources": {"stale": ["NFCI"]}})
    monkeypatch.setattr(W, "_last_fail", 0.0)
    monkeypatch.setattr(B, "refresh", lambda: B.write_snapshot({"phase": "crisis", "generated_at": 9.0}))
    assert cliente.get("/api/cartera/ciclo").get_json()["phase"] == "crisis"


def test_con_otro_calculo_en_marcha_y_sin_foto_es_503(cliente, monkeypatch):
    monkeypatch.setattr(W, "_last_fail", 0.0)
    W._lock.acquire()
    try:
        r = cliente.get("/api/cartera/ciclo")
    finally:
        W._lock.release()
    assert r.status_code == 503


def test_una_foto_corrupta_es_como_no_tenerla(tmp_path):
    p = tmp_path / "ciclo.json"
    p.write_text('{"phase": "au', encoding="utf-8")
    assert B.read_snapshot(str(p)) is None


def test_dos_escritores_no_comparten_temporal(tmp_path, monkeypatch):
    usados = []
    real = B.os.replace
    monkeypatch.setattr(B.os, "replace", lambda a, b: (usados.append(a), real(a, b)))
    B.write_snapshot({"n": 1}, str(tmp_path / "c.json"))
    B.write_snapshot({"n": 2}, str(tmp_path / "c.json"))
    assert len(set(usados)) == 2 and B.read_snapshot(str(tmp_path / "c.json")) == {"n": 2}


def test_una_serie_caida_se_apunta_y_la_foto_sale_igual(tmp_path):
    from macro import sources as S

    def getter(url):
        if "id=NFCI" in url:
            raise OSError("timeout")
        if "sdmx.oecd.org" in url:
            filas = "".join(f"X,USA,M,LI,2026-{m:02d},{100 + m / 10}\n" for m in range(1, 10))
            return "DATAFLOW,REF_AREA,FREQ,MEASURE,TIME_PERIOD,OBS_VALUE\n" + filas
        if "news.google.com" in url:
            return "<rss><channel></channel></rss>"
        sid = url.split("id=")[1].split("&")[0]
        return f"observation_date,{sid}\n2026-09-01,1.0\n2026-10-01,1.0\n"

    data, heads, status = B.collect(S.Cache(str(tmp_path), getter=getter))
    assert "NFCI" not in data and "CLI:USA" in data
    assert [s["id"] for s in status if not s["ok"]] == ["NFCI"]
    snap = B.snapshot(data, heads, status, today="2026-10-18", now=0)
    assert snap["sources"]["failed"] == ["NFCI"] and snap["phase"]


def test_main_devuelve_1_si_no_hay_foto(monkeypatch, capsys):
    def roto():
        raise B.BuildError("OCDE caída")

    monkeypatch.setattr(B, "refresh", roto)
    assert B.main() == 1 and "OCDE caída" in capsys.readouterr().err
