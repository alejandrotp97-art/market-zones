"""Simulador fiscal: lo exacto y lo estimado, y la frontera entre los dos.

La mitad de estos tests no comprueban un número: comprueban que el módulo NO
supone lo que no sabe. Es lo que separa una estimación honesta de una cifra con
aspecto de dato.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from cartera.fiscal import (
    TRAMOS_AHORRO_2025,
    fifo_preview,
    loss_offset_note,
    repurchase_risk,
    simulate_sale,
    tax_on_gain,
)


def lote(q, unit, fecha="2024-01-01"):
    return [q, unit, fecha]


# ── FIFO: lo exacto ───────────────────────────────────────────────────────
def test_la_simulacion_NO_consume_los_lotes():
    """Es una pregunta, no una operación. Si mutase la lista, preguntar «¿y si
    vendo?» dos veces daría respuestas distintas."""
    lotes = [lote(10, 10.0), lote(10, 20.0)]
    fifo_preview(lotes, 15)
    fifo_preview(lotes, 15)
    assert [l[0] for l in lotes] == [10, 10]


def test_se_consumen_los_lotes_mas_ANTIGUOS_primero():
    c, coste, faltan = fifo_preview([lote(10, 10.0, "2023-01-01"),
                                     lote(10, 20.0, "2024-01-01")], 15)
    assert [x["qty"] for x in c] == [10.0, 5.0]
    assert coste == pytest.approx(10 * 10.0 + 5 * 20.0)
    assert c[0]["date"] == "2023-01-01"
    assert c[1]["partial"] is True and c[0]["partial"] is False
    assert faltan == 0


def test_vender_mas_de_lo_que_hay_lo_dice_en_vez_de_inventarlo():
    """Devolver el coste de lo que sí hay y callarse el resto daría una
    plusvalía calculada sobre títulos que no existen."""
    c, coste, faltan = fifo_preview([lote(5, 10.0)], 12)
    assert faltan == 7
    assert sum(x["qty"] for x in c) == 5


def test_sin_lotes_no_hay_nada_que_simular():
    assert fifo_preview([], 10) == ([], 0.0, 10.0)
    assert fifo_preview([lote(5, 10.0)], 0)[0] == []


# ── el impuesto: lo estimado ──────────────────────────────────────────────
def test_los_tramos_del_ahorro_se_aplican_por_escalones():
    # 6.000 al 19% + 4.000 al 21%
    assert tax_on_gain(10000, TRAMOS_AHORRO_2025) == pytest.approx(1140 + 840)


def test_una_ganancia_NO_tributa_siempre_al_tipo_mas_bajo():
    """El error clásico es multiplicar la plusvalía por el 19%. Quien ya lleva
    ganancias ese año paga la nueva en SU tramo, no en el primero."""
    sola = tax_on_gain(5000, TRAMOS_AHORRO_2025)
    encima = tax_on_gain(5000, TRAMOS_AHORRO_2025, already=45000)
    assert sola == pytest.approx(950.0)          # 19%
    assert encima == pytest.approx(1050.0)       # 21%
    assert encima > sola


def test_una_perdida_no_genera_impuesto():
    assert tax_on_gain(-5000, TRAMOS_AHORRO_2025) == 0.0
    assert tax_on_gain(0, TRAMOS_AHORRO_2025) == 0.0


def test_los_tramos_entran_por_parametro_para_que_una_reforma_no_toque_la_formula():
    plano = [(None, 0.25)]
    assert tax_on_gain(10000, plano) == pytest.approx(2500.0)


# ── la simulación completa ────────────────────────────────────────────────
def test_una_venta_con_ganancia_separa_lo_exacto_de_lo_estimado():
    s = simulate_sale([lote(10, 100.0), lote(10, 150.0)], qty=15, price=200.0,
                      fee=10.0, fx=1.0)
    # exacto
    assert s["cost_fifo"] == pytest.approx(10 * 100 + 5 * 150)   # 1750
    assert s["proceeds"] == pytest.approx(15 * 200 - 10)         # 2990
    assert s["result"] == pytest.approx(1240.0)
    # estimado
    assert s["tax"] == pytest.approx(1240 * 0.19, abs=0.01)
    assert s["net"] == pytest.approx(2990 - s["tax"], abs=0.01)


def test_el_ingreso_se_valora_al_cambio_de_HOY():
    s = simulate_sale([lote(10, 100.0)], qty=10, price=200.0, fx=0.90)
    assert s["proceeds"] == pytest.approx(10 * 200 * 0.90)


def test_las_minusvalias_pendientes_reducen_la_base_y_lo_que_sobra_sigue_pendiente():
    s = simulate_sale([lote(10, 100.0)], qty=10, price=200.0,
                      pending_losses=2000.0)
    assert s["result"] == pytest.approx(1000.0)
    assert s["losses_used"] == pytest.approx(1000.0)
    assert s["losses_left"] == pytest.approx(1000.0)
    assert s["taxable_base"] == 0.0 and s["tax"] == 0.0


def test_las_minusvalias_no_empujan_la_base_por_debajo_de_cero():
    s = simulate_sale([lote(10, 100.0)], qty=10, price=110.0, pending_losses=99999.0)
    assert s["taxable_base"] == 0.0
    assert s["losses_used"] <= s["result"]


def test_vender_mas_de_lo_que_hay_se_reporta_y_no_se_factura():
    s = simulate_sale([lote(5, 100.0)], qty=12, price=200.0)
    assert s["short"] == 7
    assert s["qty"] == 5
    assert s["proceeds"] == pytest.approx(5 * 200.0)   # sólo lo que existe


def test_una_venta_en_perdidas_no_paga_y_explica_que_hacer_con_ella():
    s = simulate_sale([lote(10, 200.0)], qty=10, price=100.0)
    assert s["result"] < 0 and s["tax"] == 0.0
    assert s["effective_rate"] is None
    assert "cuatro ejercicios siguientes" in loss_offset_note(s["result"])


def test_con_ganancia_no_hay_nota_de_compensacion():
    assert loss_offset_note(500.0) is None


# ── la regla de los dos meses ─────────────────────────────────────────────
def test_una_recompra_cercana_se_avisa_porque_bloquea_la_perdida():
    r = repurchase_risk("2026-03-01", ["2026-02-15", "2023-01-01", "2026-01-05"])
    assert [x["date"] for x in r] == ["2026-02-15", "2026-01-05"]
    assert r[0]["days"] == 14


def test_en_un_no_cotizado_la_ventana_es_de_un_ano():
    fechas = ["2025-06-01"]
    assert repurchase_risk("2026-03-01", fechas, listed=True) == []
    assert len(repurchase_risk("2026-03-01", fechas, listed=False)) == 1


def test_sin_fechas_no_se_inventa_riesgo():
    assert repurchase_risk("2026-03-01", []) == []
    assert repurchase_risk(None, ["2026-02-01"]) == []
    assert repurchase_risk("no-es-fecha", ["2026-02-01"]) == []
