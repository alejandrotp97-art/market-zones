"""La invariante dura: `analyze(df)` sigue dando lo MISMO que antes del refactor.

`tests/test_weekly.py::test_daily_default_is_byte_identical` compara
`analyze(df)` contra `analyze(df, windows=DAILY)`, y eso NO prueba la
invariante: `analyze()` hace `w = windows or DAILY`, así que los dos lados
ejecutan el mismo `w`. Un valor mal escrito dentro de DAILY pasaría inadvertido
porque estaría igual de mal en ambos lados de la igualdad.

Aquí la referencia es EXTERNA al parámetro: la salida congelada del motor tal y
como era antes de que `windows` existiera (ver `fixtures/make_daily_golden.py`).
Si alguien cambia una constante de DAILY, o mete una regresión en la ruta
diaria, esto se cae — que es justamente lo que el otro test no podía hacer.

Cubre las tres ramas de selección de modelo: full, reduced y none.
"""
import os
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zones import analyze

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "daily_golden.npz"
CASES = ("full", "reduced", "none")
NUM_COLS = ("score", "score_raw", "stretch", "rsi", "drawdown", "trend_dev",
            "volatility", "vol_pct", "volu_pct", "climax")
STR_COLS = ("zone_name", "conviction")


@pytest.fixture(scope="module")
def golden():
    if not GOLDEN.is_file():                       # pragma: no cover
        pytest.fail(f"falta el golden {GOLDEN}; regenéralo con "
                    f"fixtures/make_daily_golden.py")
    return np.load(GOLDEN)                          # sin allow_pickle a propósito


def _frame(g, case):
    cols = {c: g[f"{case}/in/{c}"] for c in ("open", "high", "low", "close", "volume")}
    n = len(cols["close"])
    return pd.DataFrame({"date": pd.date_range("2015-01-01", periods=n, freq="B"),
                         **cols})


@pytest.mark.parametrize("case", CASES)
def test_daily_matches_pre_refactor_engine(golden, case):
    """analyze(df) reproduce exactamente la salida anterior al parámetro windows."""
    out, _ = analyze(_frame(golden, case))
    for c in NUM_COLS:
        np.testing.assert_allclose(
            out[c].to_numpy(float), golden[f"{case}/out/{c}"],
            rtol=0, atol=0, equal_nan=True,
            err_msg=f"[{case}] la columna '{c}' se desvió del motor pre-refactor")


@pytest.mark.parametrize("case", CASES)
def test_daily_labels_match_pre_refactor_engine(golden, case):
    """Las etiquetas (zona y convicción) también, no solo los números."""
    out, _ = analyze(_frame(golden, case))
    for c in STR_COLS:
        got = ["" if v is None else str(v) for v in out[c]]
        assert got == list(golden[f"{case}/out/{c}"]), f"[{case}] cambió '{c}'"


@pytest.mark.parametrize("case", CASES)
def test_summary_matches_pre_refactor_engine(golden, case):
    """El Summary de la última fila, que es lo que consume el panel."""
    _, s = analyze(_frame(golden, case))
    assert [str(s.score), str(s.zone), str(s.model)] == list(golden[f"{case}/sum"])


def test_golden_covers_every_model_branch(golden):
    """Un golden que solo pillara 'full' dejaría reduced y none sin proteger."""
    assert {str(golden[f"{c}/sum"][2]) for c in CASES} == {"full", "reduced", "none"}


# ── la ruta ligera de la inversión ──────────────────────────────────────────

def test_score_components_matches_analyze_exactly(golden):
    """`score_components()` es la vía rápida de la inversión de precio.

    Existe para no pagar zonas ni convicción en cada una de las ~60 pasadas que
    hace `compute()` y que descarta enteras. Sólo es legítima si devuelve
    EXACTAMENTE lo mismo que `analyze()` en las columnas que la inversión lee:
    en cuanto se desvíe un ULP, los precios objetivo dejan de ser los del motor.
    """
    from zones.engine import score_components
    from zones.target import _FIELDS

    for case in CASES:
        df = _frame(golden, case)
        full, _ = analyze(df)
        light = score_components(df)
        for c in _FIELDS:
            np.testing.assert_array_equal(
                light[c].to_numpy(float), full[c].to_numpy(float),
                err_msg=f"[{case}] '{c}' difiere entre la ruta ligera y analyze()")


def test_score_components_matches_analyze_on_perturbed_last_bar(golden):
    """La inversión perturba el ÚLTIMO cierre: ahí es donde tiene que coincidir."""
    from zones.engine import score_components
    from zones.target import _FIELDS

    df = _frame(golden, "full")
    li = len(df) - 1
    base = float(df.loc[li, "close"])
    for mult in (0.4, 0.75, 1.0, 1.5, 2.0):
        d = df.copy()
        d.loc[li, "close"] = base * mult
        full, _ = analyze(d)
        light = score_components(d)
        for c in _FIELDS:
            assert float(light[c].iloc[-1]) == float(full[c].iloc[-1]) or (
                np.isnan(float(light[c].iloc[-1])) and np.isnan(float(full[c].iloc[-1]))), \
                f"'{c}' difiere con el último cierre a x{mult}"
