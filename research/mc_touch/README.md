# Probabilidad de tocar el precio objetivo (Montecarlo) — BTC y ETH

Estudio, **no** función del panel. Pregunta: dado el objetivo de compra/venta que
muestra el panel (consenso M1+M3 de `zones/target.py`), ¿qué probabilidad hay de
que el precio lo **toque** en 30 o 90 días? Solo entra al panel si está
**calibrado**: cuando dice 20 %, tiene que pasar ~20 % de las veces.

## Método

- Grilla causal: un as-of cada 14 barras, historia truncada en el as-of.
- Objetivo: `zones.target.compute` (el mismo código del panel).
- Montecarlo: bootstrap estacionario por bloques (media 20 días) de los retornos
  log vistos hasta el as-of, **10 000 caminos**, deriva histórica sin tocar.
- Referencia ingenua: fórmula de reflexión GBM sin deriva con la vol del último año.
- Verdad: ¿los cierres reales en (as-of, as-of+H] tocaron el objetivo?
- Se excluyen los as-of ya dentro de la zona (P=1 por definición).

**Aproximación conocida:** el objetivo queda fijo a lo largo del camino. En la
realidad la frontera se mueve con la media móvil, así que esto mide "tocar el
precio objetivo de hoy", no "entrar en la zona". Cierres en ambos lados.

Por qué 10k caminos: el error de muestreo con p≈0.1 es ±0.3 pp; 100k/1M lo bajan
a ±0.1/±0.03 pp, muy por debajo del error del modelo, que es de varios puntos.

## Correr

```bash
.venv/bin/python research/mc_touch/mc_touch.py run      # ~10 min, rows.csv + results.json
.venv/bin/python research/mc_touch/mc_touch.py report   # re-imprime desde rows.csv
```

## Resultado (2026-11-07)

Pool BTC+ETH, Brier skill frente a la tasa base (más alto = mejor):

| lado | H | n (n_eff) | MC | GBM |
|---|---|---|---|---|
| compra | 30 | 485 (226) | +0.23 | +0.19 |
| compra | 90 | 475 (74) | +0.17 | +0.19 |
| venta | 30 | 465 (217) | +0.46 | +0.43 |
| venta | 90 | 455 (71) | +0.41 | +0.52 |

ETH compra sale **negativo** (peor que decir siempre la tasa base) en ambos H.

Calibración (pool, compra H90): predice 15 % → pasa 6 %; 24 % → 13 %; 39 % → 31 %.
Lo mismo en venta y en H30: **sobrestima sistemáticamente** en el tramo 5–50 %.

## Veredicto

**No pasa al panel.**

1. **Ordena bien, pero los números no son de fiar.** Cuando dice más, pasa más,
   pero el valor absoluto viene inflado ~1,5–2× justo en el tramo útil.
2. **No le gana a la fórmula simple.** A 30 días gana por poco; a 90 pierde contra
   GBM. El coste de 10k caminos no se justifica todavía.
3. **H90 tiene poca muestra efectiva** (~70 ventanas independientes en el pool):
   las conclusiones a 90 días son débiles.

Hipótesis de la sobrestimación (no comprobada): el bootstrap usa **toda** la
historia, incluida la vol extrema de 2014–2018, más alta que la de cada ventana
siguiente; los caminos salen demasiado abiertos. Apoya esto que GBM, con solo el
último año de vol, calibra mejor a 90 días.

## Prueba 2021+ con 50k caminos (2026-11-07)

`mc_touch.py recent` → `rows_2021.csv`, `results_2021.json`. El bootstrap solo
remuestrea retornos desde 2021-01-01 (causal); los as-of arrancan con un año de
pool (ene-2022). En las MISMAS fechas se puntúan MC-2021, MC-full (50k) y GBM.

Compra, pool BTC+ETH, Brier skill:

| H | n (n_eff) | MC-2021 | MC-full | GBM |
|---|---|---|---|---|
| 30 | 241 (113) | **+0.29** | +0.25 | +0.25 |
| 90 | 233 (36) | +0.23 | +0.24 | **+0.25** |

- A 30 días la sobrestimación casi desaparece: predice 7,6 % → pasa 6,7 %;
  13,5 % → 11 %. Antes inflaba ~2×. Apoya la hipótesis de la vol vieja.
- A 90 días GBM sigue igual o mejor, y con n_eff 36 la diferencia es ruido.
- ETH compra sigue **negativo** en los tres modelos.
- **Venta: cero toques** en 2022–2026. El precio nunca leyó Euforia en esas
  fechas (0 as-of dentro; objetivo mediano +120 % arriba). No hay eventos para
  calibrar; todos los modelos predicen algo de más y GBM es el que menos.

Veredicto: la ventana reciente **mejora** el Montecarlo a 30 días, pero solo le
saca ~0,03 de skill a la fórmula GBM, que es instantánea. Sigue sin justificar
pasar al panel. Si algún día se muestra una probabilidad, GBM con la vol del
último año es la candidata más barata con calibración equivalente.
