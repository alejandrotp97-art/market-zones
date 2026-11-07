# ¿Dónde empieza de verdad la zona? M1 vs M3 vs consenso

Mide el precio objetivo contra el precio al que el activo **entró de verdad** en
la zona después. Grilla causal (un as-of cada 63 barras, 25 años, 19 activos con
ETH-USD); en cada as-of `zones.target.compute` sobre la historia truncada; hacia
delante (≤ 2 años) la primera barra donde el score del panel cruza 20 / 80.
Error = |log(entrada / objetivo)|. Se excluyen los as-of ya dentro de la zona.

```bash
.venv/bin/python research/target_entry/target_entry.py run    # ~8 min, rows.csv
.venv/bin/python research/target_entry/target_entry.py eval   # M1 / M3 / consenso / mezcla
.venv/bin/python research/target_entry/target_entry.py band   # banda anclada en M1 vs consenso
```

## Resultado (2026-11-07), error medio fuera de muestra

| lado (score) | M1 | M3 | consenso | mezcla walk-forward (peso M1) |
|---|---|---|---|---|
| compra (panel) | **0.113** | 0.149 | 0.119 | 0.114 (0.85) |
| compra (raw) | **0.106** | 0.169 | 0.127 | 0.106 (1.00) |
| venta (panel) | **0.066** | 0.112 | 0.080 | 0.069 (0.95) |
| venta (raw) | **0.056** | 0.110 | 0.071 | 0.062 (0.90) |

Robustez (mejora M1 vs consenso, bootstrap por activo, 2006+): venta panel
+0.014 IC95 [+0.004, +0.026] y venta raw +0.016 [+0.004, +0.031], 16/19 activos;
compra raw +0.021 [+0.012, +0.033], 16/19; compra panel +0.005 [−0.001, +0.014],
neutro. Se sostiene antes y después de 2016. BTC/ETH sueltos: mixto, n 8–27.

**Decisión:** el titular pasa a M1 (M3 de respaldo si M1 no tiene solución) en
`zones/target.py::_headline`. M3 queda como contraste en la tarjeta y como anillo
hueco en el gráfico. La banda conserva K_BUY/K_SELL: anclada en M1 el `k` vigente
sigue ganando fuera de muestra al recalibrado.

Límite: mide dónde empieza la zona, no cuándo ni si se llega: en 2 años solo
entra el 40 % de los as-of en compra y el 59 % en venta.
