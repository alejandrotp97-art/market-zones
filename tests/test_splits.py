"""Splits: detectar, previsualizar y no romper nada al ajustar."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from cartera.splits import cost_is_preserved, pending, preview


def mov(i, fecha, q=10.0, px=400.0, side="buy"):
    return {"id": i, "date": fecha, "quantity": q, "price": px, "side": side}


# ── detección ─────────────────────────────────────────────────────────────
def test_un_split_posterior_a_una_compra_se_detecta():
    p = pending([mov(1, "2023-05-01")], [{"date": "2024-06-10", "ratio": 10.0}])
    assert len(p) == 1
    assert p[0]["ratio"] == 10.0 and p[0]["n_movements"] == 1
    assert p[0]["kind"] == "split" and p[0]["ids"] == [1]


def test_un_split_ANTERIOR_a_todas_las_compras_no_afecta():
    """Quien compró después ya tecleó en la escala nueva: no hay nada que
    ajustar, y avisar sería ruido permanente."""
    assert pending([mov(1, "2025-01-01")], [{"date": "2024-06-10", "ratio": 10.0}]) == []


def test_un_contrasplit_tambien_cuenta():
    p = pending([mov(1, "2023-01-01")], [{"date": "2024-01-01", "ratio": 0.25}])
    assert p[0]["kind"] == "contrasplit"


def test_un_split_ya_resuelto_deja_de_avisar():
    s = [{"date": "2024-06-10", "ratio": 10.0}]
    assert pending([mov(1, "2023-01-01")], s, acked=["2024-06-10"]) == []


def test_un_ratio_de_uno_no_es_un_split():
    assert pending([mov(1, "2023-01-01")], [{"date": "2024-01-01", "ratio": 1.0}]) == []


def test_un_movimiento_sin_fecha_no_se_da_por_anterior():
    """Sin fecha no se puede saber de qué lado del split cae. Suponer que es
    anterior lo ajustaría sin base."""
    assert pending([mov(1, "")], [{"date": "2024-06-10", "ratio": 10.0}]) == []


# ── la regla del ajuste ───────────────────────────────────────────────────
def test_el_ajuste_multiplica_titulos_y_divide_precio():
    f = preview([mov(1, "2023-05-01", q=10, px=400)],
                {"date": "2024-06-10", "ratio": 10.0})[0]
    assert f["qty_after"] == 100.0
    assert f["price_after"] == 40.0


def test_EL_COSTE_NO_SE_MUEVE_y_es_lo_que_hace_seguro_el_ajuste():
    """Se pagó lo que se pagó. Si el total cambiara, el ajuste estaría mal
    planteado y reescribiría el libro con números peores que los que tenía."""
    filas = preview([mov(1, "2023-05-01", q=10, px=400),
                     mov(2, "2023-09-01", q=3, px=520.5)],
                    {"date": "2024-06-10", "ratio": 10.0})
    assert cost_is_preserved(filas)
    assert sum(f["cost_before"] for f in filas) == pytest.approx(
           sum(f["cost_after"] for f in filas))


def test_un_contrasplit_conserva_el_coste_igual():
    filas = preview([mov(1, "2023-01-01", q=100, px=2.5)],
                    {"date": "2024-01-01", "ratio": 0.25})
    assert filas[0]["qty_after"] == 25.0 and filas[0]["price_after"] == 10.0
    assert cost_is_preserved(filas)


def test_la_previsualizacion_deja_fuera_lo_posterior_al_split():
    filas = preview([mov(1, "2023-01-01"), mov(2, "2025-01-01")],
                    {"date": "2024-06-10", "ratio": 10.0})
    assert [f["id"] for f in filas] == [1]


def test_un_coste_que_no_cuadra_se_detecta():
    malas = [{"cost_before": 100.0, "cost_after": 90.0}]
    assert cost_is_preserved(malas) is False
