# regime — motor mínimo validado (FASE 4)

La implementación de la especificación validada empíricamente (FASE 1–3). Es un
motor **causal** (sin lookahead): `score(t)` depende únicamente de datos ≤ `t`.

## Qué es

- **Score 0–100** = media equiponderada del **percentil de ventana expanding** de
  3 ejes validados: `mayer` (extensión), `realized_vol` (volatilidad, invertida),
  `drawdown` (ciclo). 0 = capitulación, 100 = euforia.
- **Régimen** = máquina de estados por regiones sobre (nivel × vol × tendencia),
  con `crosses` (inestabilidad) como desempate; histéresis + dwell mínimo.

De 11 candidatos, el análisis empírico dejó 3 imprescindibles para el Score + 1
para la FSM. Ver el dossier de validación para el porqué de cada uno.

## Diferencia clave con el motor viejo (`../zones`)

El motor legado normaliza con **z-score in-sample** → usa el futuro para juzgar el
pasado (lookahead). Aquí la normalización es **percentil expanding**, y hay un test
(`test_engine_is_causal`) que lo **demuestra**: el score en `t` es idéntico ocultando
todo lo posterior a `t`.

## Uso

```bash
PY=/home/alex/bots/btc-poly/.venv/bin/python
$PY -m regime.cli SPY
$PY -m regime.cli BTC-USD --tail 8
```

```python
from regime import analyze
frame, reading = analyze(df)          # df con columnas date, close (+ high/low)
print(reading.score, reading.regime)
```

## Estructura

```
normalize.py   expanding_percentile  (garantía no-lookahead)
indicators.py  mayer · realized_vol · drawdown · sma200_crosses · sma200_slope_up
regimes.py     mapa de regiones v1 + histéresis/dwell
engine.py      analyze(): raw → percentil → score → FSM → Reading
cli.py         entrada por ticker
tests/         8 tests (causalidad, cotas, dirección, FSM)
```

## Estado

- **Score**: validado (FASE 1–3), cerrado.
- **FSM (regímenes)**: **v2 calibrada**. Los shocks (Pánico/Clímax) exigen un *spike*
  extremo de vol (percentil ≥90), no vol crónica; Lateral exige calma. Fixes confirmados
  cross-activo: Pánico-2022 43%→23%, Lateral-2008 22%→14%. Cortes distribucionales, no
  ajustados a retorno.
- **Probabilidades condicionales** (`analogs.py`): retorno/drawdown/vol posterior a
  1s/1m/3m/6m/12m desde análogos históricos (por régimen o k-NN), con bandas percentil
  e **IC por block-bootstrap** (respeta el solapamiento de los retornos forward) + N y
  flag de confianza.

**Validación del constructo:** el retorno medio a 12m ordena los regímenes en la dirección
correcta — Pánico +19.5% (comprar miedo) … Sobrecalentamiento +8.4% (comprar euforia).

## Pendiente

- Cableado al dashboard (score + régimen + ejes + fan chart de escenarios + medidor de confianza).
