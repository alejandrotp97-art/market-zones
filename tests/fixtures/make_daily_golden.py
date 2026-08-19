"""Genera el golden de la ruta diaria a partir del motor ANTERIOR al refactor.

Por qué existe: `test_daily_default_is_byte_identical` comparaba `analyze(df)`
contra `analyze(df, windows=DAILY)`, y eso es una tautología — `analyze()` hace
`w = windows or DAILY`, así que ambos lados ejecutan el mismo `w`. Si un campo
de DAILY estuviera mal escrito, el test pasaría igual. Este fixture congela la
salida del motor tal y como era ANTES de que existiera el parámetro `windows`,
que es la única referencia independiente que prueba la invariante de verdad.

Uso (solo cuando se quiera REGENERAR a propósito, nunca en CI):

    git archive <commit-pre-refactor> | tar -x -C /tmp/pre
    python3 tests/fixtures/make_daily_golden.py /tmp/pre

El fixture guarda la serie de ENTRADA además de la salida esperada, así que el
test no depende de que el RNG de numpy siga produciendo los mismos números.

Cubre las tres ramas de selección de modelo: full (n=600), reduced (n=80) y
none (n=10).
"""
import pathlib
import sys

import numpy as np
import pandas as pd

OUT = pathlib.Path(__file__).with_name("daily_golden.npz")
CASES = {"full": 600, "reduced": 80, "none": 10}
NUM_COLS = ("score", "score_raw", "stretch", "rsi", "drawdown", "trend_dev",
            "volatility", "vol_pct", "volu_pct", "climax")
STR_COLS = ("zone_name", "conviction")


def series(n: int, seed: int) -> pd.DataFrame:
    """OHLCV sintética pero determinista; se guarda en el fixture tal cual."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0006, 0.017, n)
    close = 100.0 * np.exp(np.cumsum(steps))
    high = close * (1.0 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1.0 - np.abs(rng.normal(0, 0.004, n)))
    # `date` es COLUMNA, no índice: es lo que espera analyze() en ambas versiones
    return pd.DataFrame(
        {"date": pd.date_range("2015-01-01", periods=n, freq="B"),
         "open": np.r_[close[0], close[:-1]], "high": high, "low": low,
         "close": close, "volume": rng.integers(1_000, 50_000, n).astype(float)})


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    pre = pathlib.Path(sys.argv[1]).resolve()
    if not (pre / "zones" / "engine.py").is_file():
        sys.exit(f"no encuentro zones/engine.py bajo {pre}")
    # el motor VIEJO tiene que ganar al del repo en la resolución de imports
    sys.path.insert(0, str(pre))
    import zones.engine as eng
    from zones.engine import analyze
    if hasattr(eng, "Windows"):
        sys.exit("ese árbol YA tiene Windows: no es pre-refactor, sería circular")

    blob: dict[str, np.ndarray] = {}
    for name, n in CASES.items():
        df = series(n, seed=20260817 + n)
        out, summary = analyze(df)              # firma vieja: sin `windows`
        for c in ("open", "high", "low", "close", "volume"):
            blob[f"{name}/in/{c}"] = df[c].to_numpy(float)
        for c in NUM_COLS:
            blob[f"{name}/out/{c}"] = out[c].to_numpy(float)
        for c in STR_COLS:
            # dtype unicode y no `object`: object obligaría a allow_pickle=True
            # al cargar el fixture, y un test no debería deserializar pickles.
            blob[f"{name}/out/{c}"] = np.array(
                ["" if v is None else str(v) for v in out[c]], dtype=np.str_)
        blob[f"{name}/sum"] = np.array(
            [str(summary.score), str(summary.zone), str(summary.model)], dtype=np.str_)
        print(f"{name:8} n={n:<4} modelo={summary.model:8} score={summary.score}")

    np.savez_compressed(OUT, **blob)
    print(f"\n-> {OUT}  ({OUT.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
