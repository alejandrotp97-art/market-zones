"""El plan del inversor: aportaciones, objetivo y avisos.

Casi todo lo de aquí fija un SILENCIO o una forma de decir las cosas, no un
número. Es deliberado: un panel de avisos que habla de más deja de leerse, y a
partir de ese momento no avisa de nada aunque siga funcionando.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from cartera.plan import (AVISO, INFO, attention, contribution_stats,
                          goal_progress, monthly_flows)


def pos(tk, **kw):
    base = {"ticker": tk, "name": tk, "weight": 50.0, "market_value": 1000.0,
            "valued": True, "has_history": True, "ter": 0.2, "target": None,
            "oversold": 0.0, "why": None}
    base.update(kw)
    return base


def facts(**kw):
    base = {"positions": [pos("AAA"), pos("BBB")], "total": 2000.0,
            "n_undated": 0, "months_since_contribution": 0,
            "eff_n_corr": None, "eff_n_weights": None, "coverage_pct": 100.0}
    base.update(kw)
    return base


def claves(avisos):
    return [a["key"] for a in avisos]


# ── la forma de un aviso ──────────────────────────────────────────────────
def test_todo_aviso_dice_que_pasa_a_quien_afecta_y_por_que_importa():
    """Si falta cualquiera de las tres, el aviso no está terminado: obliga a
    quien lo lee a investigar por su cuenta qué le están contando."""
    a = attention(facts(n_undated=3, coverage_pct=70.0,
                        positions=[pos("AAA", valued=False, why="sin precio", ter=None)],
                        total=0.0))
    assert a, "algo tenía que saltar"
    for x in a:
        assert x["title"] and x["scope"] and x["why"], x
        assert x["level"] in (AVISO, INFO)


def test_ningun_aviso_es_una_orden_de_comprar_o_vender():
    """El panel tiene MEDIDO que no sabe hacer timing. Un aviso describe un
    hecho; en cuanto dice qué hacer, promete algo que no puede cumplir."""
    a = attention(facts(
        positions=[pos("AAA", weight=60.0, target=30.0, ter=None, has_history=False),
                   pos("BBB", weight=40.0, target=70.0)],
        eff_n_corr=1.2, eff_n_weights=1.9, coverage_pct=55.0,
        months_since_contribution=9, n_undated=2))
    texto = " ".join(f"{x['title']} {x['why']}" for x in a).lower()
    for verbo in ("deberías", "compra", "vende", "recomend", "conviene",
                  "aprovecha", "es momento"):
        assert verbo not in texto, f"aparece «{verbo}»"


# ── cuándo se calla ───────────────────────────────────────────────────────
def test_una_cartera_sana_no_genera_ruido():
    assert attention(facts()) == []


def test_una_desviacion_pequena_no_se_avisa():
    """Un peso nunca cae exacto en su objetivo. Sin banda muerta, el panel
    avisaría todos los días y dejaría de leerse."""
    a = attention(facts(positions=[pos("AAA", weight=52.0, target=50.0),
                                   pos("BBB", weight=48.0, target=50.0)]))
    assert a == []


def test_una_desviacion_grande_si():
    a = attention(facts(positions=[pos("AAA", weight=58.0, target=50.0),
                                   pos("BBB", weight=42.0, target=50.0)]))
    assert [k for k in claves(a) if k.startswith("drift:")] == ["drift:AAA", "drift:BBB"]
    assert "8.0 puntos más" in a[0]["title"]


def test_sin_objetivo_declarado_no_hay_desviacion_que_avisar():
    """Que nadie haya decidido un peso no significa que el actual esté mal."""
    a = attention(facts(positions=[pos("AAA", weight=95.0, target=None),
                                   pos("BBB", weight=5.0, target=None)]))
    assert not [k for k in claves(a) if k.startswith("drift:")]


# ── lo que sí es grave ────────────────────────────────────────────────────
def test_una_posicion_sin_valorar_es_AVISO_y_dice_que_falta():
    a = attention(facts(positions=[pos("AAA", valued=False, why="sin tipo de cambio")]))
    x = [i for i in a if i["key"] == "sin_valorar"][0]
    assert x["level"] == AVISO
    assert "sin tipo de cambio" in x["missing"]
    assert "menor que el que tienes" in x["why"]


def test_las_ventas_de_mas_se_tratan_como_error_de_DATOS():
    a = attention(facts(positions=[pos("AAA", oversold=3.0)]))
    x = [i for i in a if i["key"] == "oversold"][0]
    assert x["level"] == AVISO
    assert "falta alguna compra" in x["why"]


def test_sin_historico_sube_a_aviso_solo_si_pesa():
    poco = attention(facts(positions=[pos("AAA", weight=5.0, market_value=100.0, has_history=False),
                                      pos("BBB", weight=95.0, market_value=1900.0)]))
    mucho = attention(facts(positions=[pos("AAA", weight=50.0, market_value=1000.0, has_history=False),
                                       pos("BBB", weight=50.0, market_value=1000.0)]))
    assert [i for i in poco if i["key"] == "sin_historico"][0]["level"] == INFO
    assert [i for i in mucho if i["key"] == "sin_historico"][0]["level"] == AVISO


def test_sin_ter_no_dice_que_no_cueste_nada():
    a = attention(facts(positions=[pos("AAA", ter=None), pos("BBB")]))
    x = [i for i in a if i["key"] == "sin_ter"][0]
    assert "no se sabe cuánto" in x["why"]
    assert "KID" in x["missing"]


def test_los_avisos_van_antes_que_los_informativos():
    a = attention(facts(positions=[pos("AAA", valued=False, why="sin precio"),
                                   pos("BBB", weight=60.0, target=40.0)]))
    niveles = [x["level"] for x in a]
    assert niveles == sorted(niveles, key=lambda l: 0 if l == AVISO else 1)


# ── calendario de aportaciones ────────────────────────────────────────────
def test_los_meses_vacios_aparecen_a_cero():
    """Comprimir el calendario saltándose los meses sin nada pega una barra a
    la siguiente y hace parecer constante un ritmo que tuvo parones."""
    filas = monthly_flows(["2024-01-15", "2024-04-10"], [100.0, 200.0])
    assert [r["month"] for r in filas] == ["2024-01", "2024-02", "2024-03", "2024-04"]
    assert [r["in"] for r in filas] == [100.0, 0.0, 0.0, 200.0]


def test_entradas_y_salidas_no_se_netean():
    """«Aporté 1.000 y retiré 1.000» y «no hice nada» son dos meses muy
    distintos, y el neto los confunde en uno solo."""
    filas = monthly_flows(["2024-01-05", "2024-01-20"], [1000.0, -1000.0])
    assert filas[0]["in"] == 1000.0 and filas[0]["out"] == 1000.0
    assert filas[0]["net"] == 0.0


def test_los_dividendos_van_a_su_propia_columna():
    filas = monthly_flows(["2024-01-05"], [0.0], [45.0])
    assert filas[0]["div"] == 45.0 and filas[0]["in"] == 0.0


def test_la_media_mensual_reparte_entre_los_meses_TRANSCURRIDOS():
    """Dividir sólo entre los meses en que se aportó contesta «cuánto aporto
    cuando aporto», que no es lo que nadie quiere saber — y sale más alto."""
    filas = monthly_flows(["2024-01-15", "2024-04-10"], [100.0, 200.0])
    s = contribution_stats(filas)
    assert s["months"] == 4 and s["n_months_with_in"] == 2
    assert s["avg_month"] == 75.0          # 300/4, no 300/2


def test_los_meses_desde_la_ultima_aportacion_se_cuentan_hasta_hoy():
    filas = monthly_flows(["2024-01-15", "2024-04-10"], [100.0, 200.0])
    assert contribution_stats(filas, today="2024-09")["months_since"] == 5


def test_un_calendario_vacio_no_inventa_medias():
    s = contribution_stats([])
    assert s["avg_month"] is None and s["months_since"] is None


# ── objetivo propio ───────────────────────────────────────────────────────
def test_el_progreso_es_un_hecho_y_no_una_fecha_de_llegada():
    """Decir «a este ritmo llegas en 2034» exige suponer una rentabilidad
    futura. Este panel tiene medido que no sabe pronosticar."""
    g = goal_progress({"capital": 100000.0, "monthly": 500.0}, value=37000.0,
                      contributed_12m=5520.0)
    assert g["pct"] == 37.0
    assert g["missing"] == 63000.0
    assert g["plan_12m"] == 6000.0 and g["plan_pct"] == 92.0
    assert not any("año" in str(k) or "eta" in str(k) for k in g)


def test_sin_objetivo_declarado_no_hay_nada_que_medir():
    assert goal_progress(None, value=1000.0) is None
    assert goal_progress({}, value=1000.0) is None


def test_sin_aportacion_prevista_se_mide_el_capital_igualmente():
    g = goal_progress({"capital": 50000.0}, value=10000.0)
    assert g["pct"] == 20.0 and "plan_pct" not in g


def test_un_reparto_50_50_no_es_concentracion():
    """En una cartera de dos posiciones, CUALQUIER reparto deja las dos por
    encima de un tercio. Avisar de que un 50/50 está concentrado es ruido: ese
    reparto es una decisión, no un descuido."""
    assert attention(facts(positions=[pos("AAA", weight=50.0), pos("BBB", weight=50.0)])) == []


def test_el_mismo_peso_SI_es_concentracion_con_muchas_posiciones():
    """50% entre dos es la mitad; 50% entre ocho es que siete no pintan nada."""
    otras = [pos(f"P{i}", weight=50.0 / 7, market_value=100.0) for i in range(7)]
    a = attention(facts(positions=[pos("AAA", weight=50.0, market_value=700.0)] + otras,
                        total=1400.0))
    assert "concentracion:AAA" in claves(a)
    x = [i for i in a if i["key"] == "concentracion:AAA"][0]
    assert "reparto igual daría" in x["why"]


def test_un_split_sin_resolver_es_AVISO_y_explica_que_rompe():
    """Es el único de la lista que puede dividir el patrimonio por diez sin dar
    un solo error, así que no puede quedarse en informativo."""
    a = attention(facts(splits=[{"ticker": "AAA", "date": "2024-06-10", "ratio": 10.0,
                                 "kind": "split", "n_movements": 2, "qty_now": 10}]))
    x = [i for i in a if i["key"].startswith("split:")][0]
    assert x["level"] == AVISO
    assert "divididos por 10" in x["why"]
    assert "no se puede deducir del número" in x["missing"]
