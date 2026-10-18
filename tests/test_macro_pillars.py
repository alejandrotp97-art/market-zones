"""Pilares del ciclo: cada indicador se puntúa en [-1, 1] y se agrupa por pilar.

Lo que se protege: que evaluar en una fecha pasada no mire datos posteriores
(sin eso, el historial del ciclo sería una profecía cumplida), que un dato
demasiado viejo no se haga pasar por actual, y que un pilar sin datos no cuente
como neutro sino que se quede fuera y el resto se reparta su peso.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from macro import pillars as P


def test_value_at_no_mira_el_futuro_y_caduca():
    s = [("2026-01-01", 1.0), ("2026-02-01", 2.0), ("2026-03-01", 3.0)]
    assert P.value_at(s, "2026-02-15") == ("2026-02-01", 2.0)
    assert P.value_at(s, "2025-12-31") is None
    assert P.value_at(s, "2026-08-01", max_age_days=60) is None
    assert P.value_at(s, "2026-08-01") == ("2026-03-01", 3.0)


def test_yoy_compara_con_el_dato_de_hace_un_anyo():
    s = [("2025-03-01", 100.0), ("2025-09-01", 103.0), ("2026-03-01", 104.0)]
    assert P.transform(s, "yoy", "2026-03-01") == pytest.approx(4.0)
    assert P.transform(s, "chg_12m", "2026-03-01") == pytest.approx(4.0)
    assert P.transform(s[1:], "yoy", "2026-03-01") is None      # sin el de hace un año


def test_distancia_a_la_media_de_200_y_caida_desde_maximos():
    s = [(f"2026-01-{d:02d}", v) for d, v in [(1, 100.0), (2, 120.0), (3, 90.0)]]
    assert P.transform(s, "dd_52w", "2026-01-03") == pytest.approx(-25.0)
    assert P.transform(s, "pct_ma200", "2026-01-03") == pytest.approx((90 / (310 / 3) - 1) * 100)


def test_diferencial_alinea_cada_fecha_con_el_ultimo_dato_del_otro():
    a = [("2026-01-01", 4.0), ("2026-01-02", 4.5)]
    b = [("2026-01-01", 3.0)]
    assert P.spread(a, b) == [("2026-01-01", 1.0), ("2026-01-02", 1.5)]


def test_puntuacion_acotada_y_con_el_signo_del_sentido_bueno():
    vix = P.BY_KEY["vix"]
    assert P.score(vix, vix.neutral) == 0
    assert P.score(vix, 80) < -0.99 and P.score(vix, 10) > 0
    assert -1 <= P.score(vix, 1e9) <= 1


def _data():
    dias = [f"2026-0{m}-01" for m in range(1, 10)]
    return {
        "VIXCLS": [(d, 40.0) for d in dias],                # pánico
        "CLI:USA": [(d, 100.0 + 0.1 * i) for i, d in enumerate(dias)],
    }


def test_un_pilar_sin_datos_queda_fuera_y_no_cuenta_como_neutro():
    r = P.evaluate(_data(), "2026-09-01")
    pil = {p["key"]: p for p in r["pillars"]}
    assert pil["actividad"]["score"] is None
    assert all(i["value"] is None for i in pil["actividad"]["indicators"])
    vivos = [p for p in r["pillars"] if p["score"] is not None]
    w = sum(p["weight"] for p in vivos)
    esperado = sum(p["weight"] * p["score"] for p in vivos) / w
    assert r["composite"] == pytest.approx(esperado)
    assert pil["riesgo"]["score"] == pytest.approx(P.score(P.BY_KEY["vix"], 40.0))


def test_evaluar_en_el_pasado_ignora_lo_posterior():
    data = _data()
    data["VIXCLS"] = [("2026-01-01", 12.0), ("2026-01-05", 12.0), ("2026-06-01", 60.0)]
    r = P.evaluate(data, "2026-01-05")
    ind = next(i for p in r["pillars"] for i in p["indicators"] if i["key"] == "vix")
    assert ind["value"] == 12.0 and ind["as_of"] == "2026-01-05"
    assert ind["score"] > 0


def test_sin_fecha_evalua_con_lo_ultimo_disponible():
    r = P.evaluate(_data())
    assert r["as_of"] == "2026-09-01"
    assert not math.isnan(r["composite"])
