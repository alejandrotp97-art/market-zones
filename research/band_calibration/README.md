# Calibración de la banda de incertidumbre del precio objetivo

La banda que dibuja el panel alrededor del precio objetivo (`zones/target.py`,
constantes `K_BUY`, `K_SELL`) no es un adorno: sale de un estudio causal de 25 años.
Esta carpeta guarda ese estudio para poder **re-validarlo una vez al año**.

## Qué es la banda

El objetivo (M1 desde 2026-11-07; antes la mediana M1+M3) es la **entrada** de la zona. El precio casi nunca se
detiene ahí: la cruza y sigue corriendo hacia dentro. La banda muestra hasta dónde
suele correr:

    profundidad_suelo = K_BUY  · vol_anualizada     (compra: hacia abajo)
    altura_techo      = K_SELL · vol_anualizada     (venta: hacia arriba)

`vol_anualizada` = desvío de los retornos log del último año, anualizado (252 barras
diarias / 52 semanales). Escala con el timeframe, así la banda es comparable.

## Valores vigentes

| constante | valor | qué mapea |
|---|---|---|
| `K_BUY`  | 0.64 | vol → profundidad del suelo |
| `K_SELL` | 1.16 | vol → altura del techo |

Confianza por vol anualizada: `< 25%` fiable · `< 45%` media · resto amplia.

## Por qué se puede confiar (y qué NO hacer)

- La vol realizada **predice** la dispersión de la perforación: Spearman **+0.78**
  (suelo) y **+0.82** (techo), p < 0.001, sobre 1526 muestras / 18 activos.
- **NO recalibrar seguido.** Un walk-forward out-of-sample mostró que recalibrar
  `k` semanalmente **empeora** el suelo (−5.9% MAE; −9.6% en índices) por
  bias-variance, y solo roza el techo (+2.3%, y es artefacto de cola). La señal
  tiene **2 años de latencia** (cada muestra necesita su ventana forward), así que
  el precio de hoy recién madura como dato de calibración dos años después.
- `k_buy` vaga ±10% alrededor de 0.64 **sin tendencia**; `k_sell` deriva lento
  (~5%/año). Una revisión **anual** captura toda la deriva relevante.

## Re-validación anual (o al ampliar el universo)

Correr en orden desde esta carpeta (necesita `pandas`, `numpy`, `scipy`):

```bash
cd research/band_calibration
python3 target_multidate.py run        # ~5 min, genera multidate_rows.csv
python3 aggregate_multidate.py         # escalera suelo/techo por tramo
python3 band_prototype_data.py         # Spearman + k_buy/k_sell sugeridos
python3 walkforward_recalib.py         # confirma fija vs semanal OOS
```

Si `k_buy`/`k_sell` de `band_prototype_data.py` se movieron **> ~10%** respecto de
los valores vigentes, actualizarlos en `zones/target.py` y anotar la fecha acá.
Si se movieron menos, no tocar nada: es ruido.

### Bitácora

- 2026-08-16 — Calibración inicial. K_BUY=0.64, K_SELL=1.16. Spearman +0.78/+0.82.
  Universo: ^GSPC ^RUT ^IXIC AAPL MSFT WMT JNJ V BRK-B NVDA MU LLY TSLA EAT UMBF
  VSAT MOG-A BTC-USD (18 activos, grilla trimestral 25 años, ventana forward 2 años).
- 2026-11-07 — Walk-forward fuera de muestra (`band_walkforward.py`, 19 activos con
  ETH-USD, vol del as-of como en el panel): ningún candidato le gana al `k` fijo
  (refit global, por clase, potencia `a·vol^b`, vol 63/756, factores extra,
  ventanas recientes). El objetivo pasó a M1 (`research/target_entry/`); con M1 de
  ancla el `k` vigente sigue ganando fuera de muestra (compra 0.0722 vs 0.0750
  refit; venta 0.337 vs 0.339). K_BUY/K_SELL **sin cambios**.

## Walk-forward (2026-11-07)

```bash
.venv/bin/python research/band_calibration/band_walkforward.py run     # ~7 min
.venv/bin/python research/band_calibration/band_walkforward.py eval    # candidatos × ventana de vol
.venv/bin/python research/band_calibration/band_walkforward.py extra   # factores extra
```

Métrica: pinball q=0.5 sobre la profundidad real entre los toques; cobertura =
proporción de toques que quedó dentro de la banda. Un as-of del año Y solo
entrena con muestras cuya ventana de 2 años ya había cerrado antes del 1-ene de Y.
