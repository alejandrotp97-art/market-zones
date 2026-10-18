"""Clasificador del ciclo: reloj de la OCDE + alarmas de estrés → fase.

Lo que se protege: que cada cuadrante del reloj dé su fase, que las alarmas
graves empujen la lectura hacia la crisis y nunca hacia el auge, que un dato
mensual no se use antes de publicarse, y que el historial no mire el futuro.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from macro import cycle as C
from macro import pillars as P


@pytest.mark.parametrize("level,mom,fase", [
    (101.0, 0.1, "auge"), (101.0, -0.1, "recesion"), (99.0, -0.1, "crisis"), (99.0, 0.1, "recuperacion"),
])
def test_cada_cuadrante_del_reloj_es_su_fase(level, mom, fase):
    p = C.base_probs(level, mom)
    assert max(p, key=p.get) == fase
    assert sum(p.values()) == pytest.approx(1)


def test_las_alarmas_graves_empujan_hacia_la_crisis():
    base = C.base_probs(101.0, 0.1)
    graves = [{"key": k, "severe": True} for k in ("sahm", "hy", "vix")]
    p = C.apply_stress(base, graves, composite=0.0)
    assert p["crisis"] > base["crisis"] and p["auge"] < base["auge"]
    assert max(p, key=p.get) != "auge"
    assert sum(p.values()) == pytest.approx(1)
    aviso = C.apply_stress(base, [{"key": "curve", "severe": False}], composite=0.0)
    assert aviso == pytest.approx(base)                 # un aviso se enseña, no mueve la fase


def test_las_alarmas_salen_de_los_valores_evaluados():
    ev = P.evaluate({"SAHMREALTIME": [("2026-01-01", 0.6)], "T10Y3M": [("2026-03-01", -0.2)]}, "2026-03-01")
    al = {a["key"]: a for a in C.alarms(ev)}
    assert al["sahm"]["severe"] is True and al["sahm"]["value"] == 0.6
    assert al["curve"]["severe"] is False
    assert "vix" not in al


def test_el_dato_mensual_no_se_usa_antes_de_publicarse():
    cli = [("2026-07-01", 100.0), ("2026-08-01", 100.2), ("2026-09-01", 100.5)]
    assert C.clock_point(cli, "2026-09-20")["date"] == "2026-08-01"
    assert C.clock_point(cli, "2026-10-18")["date"] == "2026-09-01"
    sahm = [("2026-08-01", 0.1), ("2026-09-01", 0.9)]
    ind = next(i for p in P.evaluate({"SAHMREALTIME": sahm}, "2026-09-20")["pillars"]
               for i in p["indicators"] if i["key"] == "sahm")
    assert ind["value"] == 0.1


def _serie_cli():
    # sube hasta 2026-06 y luego cae por debajo de 100
    vals = [99.0, 99.4, 99.8, 100.2, 100.6, 100.8, 100.6, 100.2, 99.7, 99.3]
    return [(f"2025-{m:02d}-01", v) for m, v in zip(range(1, 11), vals, strict=True)]


def test_el_historial_es_mensual_y_no_mira_el_futuro():
    data = {"CLI:USA": _serie_cli()}
    h = C.history(data, start="2025-05", end="2025-12")
    assert [x["month"] for x in h][:2] == ["2025-05", "2025-06"]
    assert h[-1]["month"] == "2025-12"
    # el punto de cada mes es el CLI ya publicado a fin de ese mes: el de mayo
    # (fechado el 1 de mayo) sale a mediados de junio, así que en mayo manda abril
    assert h[0]["clock_date"] == "2025-04-01"
    assert all(x["clock_date"] < x["month"] for x in h if x["clock_date"])
    fases = [x["phase"] for x in h if x["phase"]]
    assert "auge" in fases and fases[-1] in ("crisis", "recesion")


def test_la_foto_final_lleva_fase_confianza_probabilidades_y_estela():
    data = {"CLI:USA": _serie_cli(), "VIXCLS": [("2025-12-30", 35.0)]}
    r = C.classify(data, date="2025-12-31")
    assert r["phase"] in C.PHASE_KEYS
    assert r["confidence"] == pytest.approx(max(r["probs"].values()))
    assert any(a["key"] == "vix" and a["severe"] for a in r["alarms"])
    assert r["clock"]["level"] == 99.3 and len(r["trail"]) >= 2


def test_debajo_y_cayendo_sin_estres_es_recesion_y_con_estres_crisis():
    # El reloj mide el ciclo de CRECIMIENTO: crecer por debajo de la tendencia
    # no es una crisis. Sin esto, 78 meses de expansión del NBER salían «Crisis».
    q = C.base_probs(99.0, -0.1)
    calma = C.final_probs(q, [], composite=0.1)
    assert max(calma, key=calma.get) == "recesion"
    graves = [{"key": "vix", "severe": True}, {"key": "hy", "severe": True}]
    tension = C.final_probs(q, graves, composite=-0.5)
    assert max(tension, key=tension.get) == "crisis"
    assert sum(tension.values()) == pytest.approx(1)


def test_un_impulso_absurdo_no_desborda():
    p = C.base_probs(100.0, -1e6)
    assert sum(p.values()) == pytest.approx(1)


def test_el_historial_de_la_foto_lleva_la_onda():
    # La gráfica es una onda: CLI − 100 mes a mes, con su fase. Cada punto es el
    # CLI ya publicado a fin de ese mes, el mismo que decidió su fase.
    r = C.classify({"CLI:USA": _serie_cli()}, date="2025-12-31", start="2025-05")
    h = {x["month"]: x for x in r["history"]}
    assert h["2025-05"]["level"] == 100.2 and h["2025-12"]["level"] == 99.3
    assert h["2025-12"]["momentum"] == pytest.approx((99.3 - 100.6) / 3)
    assert all(set(x) >= {"month", "phase", "level", "momentum", "composite"} for x in r["history"])
