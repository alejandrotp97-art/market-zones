"""Weekly timeframe: parameterized engine windows + daily->weekly resampling.

Two guarantees anchor this feature:

  1. DAILY IS UNTOUCHED — analyze(df) and analyze(df, windows=DAILY) return the
     exact same frame. The default path is byte-identical to before the
     `windows` parameter existed, so no existing chart, test, or friend instance
     moves.
  2. WEEKLY MEANS WEEKLY — the WEEKLY preset genuinely swaps the horizons, so a
     bar count that is only "reduced model" on daily windows becomes a full
     weekly regime read, and to_weekly aggregates OHLCV the standard way.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zones import DAILY, WEEKLY, analyze, to_weekly
from zones.target import compute as target_compute


def _series(n=2000, seed=5):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))
    return pd.DataFrame({"date": pd.bdate_range("2010-01-01", periods=n),
                         "close": close,
                         "volume": rng.integers(1e6, 5e6, n)})


def test_daily_default_is_byte_identical():
    """analyze(df) == analyze(df, windows=DAILY): the default resolves to DAILY."""
    df = _series(n=1500)
    base, sb = analyze(df)
    dupe, sd = analyze(df, windows=DAILY)
    for col in ("score", "score_raw", "stretch", "rsi", "drawdown",
                "trend_dev", "volatility"):
        np.testing.assert_allclose(base[col].to_numpy(float),
                                   dupe[col].to_numpy(float), equal_nan=True)
    assert base["zone_name"].tolist() == dupe["zone_name"].tolist()
    assert (sb.score, sb.zone, sb.model) == (sd.score, sd.zone, sd.model)


def test_weekly_windows_actually_differ():
    """A bar count that is only 'reduced' under daily windows is 'full' under
    weekly ones — proof the preset changes the horizons, not just a label."""
    df = _series(n=120)                       # 120 bars
    _, daily = analyze(df)                    # daily needs 452 -> reduced
    _, weekly = analyze(df, windows=WEEKLY)   # weekly needs 92 -> full
    assert daily.model == "reduced"
    assert weekly.model == "full"
    assert weekly.score is not None and np.isfinite(weekly.score)


def test_weekly_end_to_end_from_resample():
    """Resample a real-length daily series and score it weekly: zones present,
    far fewer bars than the daily frame."""
    df = _series(n=1500)                      # ~6 years of business days
    wk = to_weekly(df)
    assert 250 < len(wk) < 340                # ~52 weeks/yr, not 1500
    frame, s = analyze(wk, windows=WEEKLY)
    assert s.model == "full"
    assert s.zone_name in {"Capitulación", "Acumulación", "Equilibrio",
                           "Precaución", "Euforia"}
    assert frame["zone"].notna().any()


def test_to_weekly_aggregation():
    """open=first, high=max, low=min, close=last, volume=sum, W-SUN labels."""
    dates = pd.bdate_range("2024-01-01", periods=10)   # Mon Jan1..Fri Jan12
    df = pd.DataFrame({
        "date": dates,
        "open":   [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "high":   [2, 3, 4, 9, 5, 7, 8, 9, 10, 11],
        "low":    [1, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        "close":  [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "volume": [10] * 10,
    })
    wk = to_weekly(df)
    assert len(wk) == 2
    assert (wk["date"].dt.dayofweek == 6).all()        # every label is a Sunday
    row1, row2 = wk.iloc[0], wk.iloc[1]
    assert (row1["open"], row1["high"], row1["low"], row1["close"],
            row1["volume"]) == (1, 9, 1, 5, 50)        # Jan 1-5
    assert (row2["open"], row2["high"], row2["low"], row2["close"],
            row2["volume"]) == (6, 11, 5, 10, 50)      # Jan 8-12


def test_to_weekly_survives_missing_volume():
    """A futures line whose volume was dropped upstream still resamples."""
    df = _series(n=60).drop(columns=["volume"])
    wk = to_weekly(df)
    assert "volume" not in wk.columns
    assert "close" in wk.columns and len(wk) > 0
    frame, s = analyze(wk, windows=WEEKLY)             # engine tolerates no volume
    assert s.model in {"none", "reduced", "full"}


def test_to_weekly_empty_frame():
    out = to_weekly(pd.DataFrame(columns=["date", "close"]))
    assert out.empty


def test_target_daily_default_is_byte_identical():
    """target.compute(df) == target.compute(df, windows=DAILY): default = DAILY,
    so the daily target-price levels are untouched."""
    df = _series(n=700)
    a = target_compute(df, "TEST")
    b = target_compute(df, "TEST", windows=DAILY)
    assert a is not None and b is not None
    assert a["model"] == b["model"] == "full"
    assert a["buy"]["m1"] == b["buy"]["m1"]
    assert a["sell"]["m1"] == b["sell"]["m1"]
    assert a["buy"]["m3"] == b["buy"]["m3"]        # M3 analog uses the MA window


def _series_vol(sigma, n=900, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, sigma, n)))
    return pd.DataFrame({"date": pd.bdate_range("2012-01-01", periods=n),
                         "close": close,
                         "volume": rng.integers(1e6, 5e6, n)})


def test_band_is_ordered_and_labeled():
    """El objetivo es la ENTRADA; la banda corre hacia dentro de la zona:
    compra por debajo del consenso, venta por encima, con vol y confianza."""
    blk = target_compute(_series_vol(0.012), "TEST")
    assert blk is not None
    assert blk["vol"] is not None and blk["conf"] in {"fiable", "media", "amplia"}
    b, s = blk["buy"], blk["sell"]
    if b["consensus"] is not None and b["band"] is not None:
        assert b["band"] < b["consensus"]          # fondo típico DEBAJO de la entrada
        assert b["band_pct"] < 0
    if s["consensus"] is not None and s["band"] is not None:
        assert s["band"] > s["consensus"]          # techo típico ENCIMA de la entrada
        assert s["band_pct"] > 0


def test_band_width_scales_with_volatility():
    """La banda es ancho = k · vol: más volátil -> banda más ancha. Es la
    calibración del estudio (Spearman +0.78), no una etiqueta fija."""
    calm = target_compute(_series_vol(0.008), "CALM")
    wild = target_compute(_series_vol(0.030), "WILD")
    assert calm["vol"] < wild["vol"]
    assert abs(calm["buy"]["band_pct"]) < abs(wild["buy"]["band_pct"])


def test_band_never_goes_to_a_negative_price():
    """Vol extrema (>140% anual) hacía k·vol > 1 y el suelo caía a un PRECIO
    NEGATIVO. MAX_DEPTH lo topa: el nivel siempre es un precio positivo."""
    for sigma in (0.03, 0.08, 0.12, 0.18):
        blk = target_compute(_series_vol(sigma), "WILD")
        if blk is None:
            continue
        b = blk["buy"]["band"]
        if b is not None:
            assert b > 0, f"sigma={sigma} dio un precio de banda no positivo: {b}"
            assert b < blk["buy"]["consensus"]


def test_curve_range_contains_the_bands():
    """Las bandas son contenido del gráfico: si caen fuera del rango de la curva
    se dibujan recortadas contra el borde."""
    for sigma in (0.008, 0.012, 0.045):
        blk = target_compute(_series_vol(sigma), "X")
        xs = [p[0] for p in blk["curve"]]
        lo, hi = min(xs), max(xs)
        if blk["buy"]["band"] is not None:
            assert blk["buy"]["band"] >= lo
        if blk["sell"]["band"] is not None:
            assert blk["sell"]["band"] <= hi


def test_band_vol_ignores_bad_bars_without_faking_a_return():
    """Una barra <=0 invalida sus dos retornos y se descarta; filtrar las barras
    antes del diff empalmaría el hueco y fabricaría un retorno inexistente."""
    clean = _series_vol(0.012, n=600)
    holed = clean.copy()
    holed.loc[300, "close"] = 0.0
    a, b = target_compute(clean, "A"), target_compute(holed, "B")
    assert a["vol"] is not None and b["vol"] is not None
    # la barra corrupta está lejos de la ventana de vol (último año): no la mueve
    assert abs(a["vol"] - b["vol"]) < 1.0


def test_sub_dollar_prices_keep_significant_digits():
    """Un activo sub-dólar no puede reportar niveles de 0.0: con 2 decimales fijos
    todo precio bajo medio centavo colapsa a cero."""
    rng = np.random.default_rng(11)
    close = 0.004 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, 800)))
    df = pd.DataFrame({"date": pd.bdate_range("2013-01-01", periods=800),
                       "close": close, "volume": rng.integers(1e6, 5e6, 800)})
    blk = target_compute(df, "TOKEN")
    assert blk["price"] > 0
    for s in (blk["buy"], blk["sell"]):
        for key in ("m1", "consensus", "band"):
            if s[key] is not None:
                assert s[key] > 0, f"{key} colapsó a {s[key]}"


def test_conf_is_absent_when_vol_is_not_measurable():
    """No declarar confianza sin medición: si no hay vol, tampoco chip."""
    blk = target_compute(_series_vol(0.012), "X")
    assert (blk["vol"] is None) == (blk["conf"] is None)


def test_target_daily_band_does_not_break_byte_identity():
    """Los campos previos (m1/m3/consensus) siguen idénticos con windows=DAILY:
    la banda es aditiva, no altera el objetivo."""
    df = _series(n=700)
    a = target_compute(df, "TEST")
    b = target_compute(df, "TEST", windows=DAILY)
    assert a["buy"]["m1"] == b["buy"]["m1"]
    assert a["sell"]["consensus"] == b["sell"]["consensus"]
    assert a["buy"]["band"] == b["buy"]["band"]     # banda también determinista


def test_target_weekly_inverts_on_weekly_bars():
    """Weekly target-price runs the inversion on W-SUN bars with weekly windows:
    a real block, buy below spot, sell above (zone-boundary monotonicity)."""
    wk = to_weekly(_series(n=1500))
    blk = target_compute(wk, "TEST", windows=WEEKLY)
    assert blk is not None and blk["model"] == "full"
    price = blk["price"]
    if blk["buy"]["m1"] is not None:
        assert blk["buy"]["m1"] < price            # to READ capitulación -> lower price
    if blk["sell"]["m1"] is not None:
        assert blk["sell"]["m1"] > price           # to READ euforia -> higher price


def test_conviction_windows_come_from_the_preset():
    """La capa de convicción tiene que escalar con el marco temporal.

    Se quedaba con sus defaults diarios (20 barras de volatilidad, 50 de
    volumen) hiciera lo que hiciera el preset, así que en semanal el clímax
    miraba 20 y 50 SEMANAS mientras la pata de volatilidad del score miraba 4.
    Comparar contra un WEEKLY al que se le fuerzan las ventanas diarias falla si
    alguien vuelve a desconectar el cableado.
    """
    import dataclasses
    wk = to_weekly(_series(n=1600, seed=7))
    daily_windows = dataclasses.replace(WEEKLY, vol_window=20, volume_window=50)
    new, _ = analyze(wk, windows=WEEKLY)
    old, _ = analyze(wk, windows=daily_windows)

    for col in ("vol_pct", "climax"):
        a = new[col].to_numpy(float)
        b = old[col].to_numpy(float)
        both = np.isfinite(a) & np.isfinite(b)
        assert both.sum() > 50, f"muestra insuficiente para juzgar '{col}'"
        assert np.abs(a[both] - b[both]).max() > 1.0, (
            f"'{col}' no se mueve al cambiar la ventana: el preset no llega "
            f"a la capa de convicción")


def test_daily_conviction_windows_are_the_historical_literals():
    """DAILY no puede cambiar: sus ventanas son las que había cableadas."""
    assert (DAILY.vol_window, DAILY.volume_window) == (20, 50)
    assert (WEEKLY.vol_window, WEEKLY.volume_window) == (4, 10)   # 20/5 y 50/5
