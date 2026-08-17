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
