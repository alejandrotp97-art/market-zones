# Calibración de la banda de incertidumbre del precio objetivo

La banda que dibuja el panel alrededor del precio objetivo (`zones/target.py`,
constantes `K_BUY`, `K_SELL`) no es un adorno: sale de un estudio causal de 25 años.
Esta carpeta guarda ese estudio para poder **re-validarlo una vez al año**.

## Qué es la banda

El objetivo (consenso M1+M3) es la **entrada** de la zona. El precio casi nunca se
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
