# El apartado «Cartera» de market-zones

> **Documento de traspaso.** Describe qué hay construido, con qué reglas y por
> qué, para que alguien pueda proponer mejoras sin volver a descubrir lo que ya
> se descubrió. **No contiene ni un dato de la cartera real**: ni un símbolo, ni
> un importe. Todos los ejemplos son inventados.

Estado a **2026-08-19**. Python 3.14.7, numpy 2.5.2, pandas 3.0.5.
471 tests, herméticos.

---

## 1. Qué es esto, y qué NO es

Es el libro de movimientos de una persona, valorado en euros en tiempo real,
más las lecturas que se pueden derivar de él. Vive dentro de un panel cuyo
núcleo es un **índice de «caro/barato»** (el Panel de Zonas) y un **motor de
régimen de mercado**, y desde 2026-08-18 las dos mitades se cruzan.

**No es una herramienta de timing, y eso está medido, no supuesto.** Un backtest
sobre S&P 500 y BTC concluyó que el *buy & hold* gana a cualquier regla de
entrada y salida probada aquí, y que la pata corta añade riesgo sin retorno.
Cualquier propuesta que consista en «avisar de cuándo comprar o vender» choca
contra evidencia ya recogida. La aplicación describe **estado**, no pronóstico.

**Restricción dura del proyecto: la cartera no se publica.** El `.gitignore`
excluye `cartera.db`, las copias y el estado de las alertas; hay un guard que
devuelve 403 en todas las rutas de cartera si el panel se sirve bajo un nombre
público sin declarar que hay autenticación delante.

---

## 2. Modelo de datos

SQLite local (`cartera.db`, permisos 0600, re-aplicados en cada conexión).

### `movements` — el libro

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `date` | TEXT | `YYYY-MM-DD`. Puede estar vacío. |
| `ticker` | TEXT | símbolo de Yahoo, en mayúsculas |
| `name` | TEXT | nombre comercial, para fondos con símbolo opaco |
| `kind` | TEXT | ETF / Fondo / Acción / ETC / … |
| `side` | TEXT | **`buy` · `sell` · `div`** |
| `quantity` | REAL | siempre positiva; el signo vive en `side` |
| `price` | REAL | **en la divisa NATIVA del instrumento** |
| `fee` | REAL | comisión; en un `div`, es la **retención** |
| `note` | TEXT | |
| `created` | TEXT | |

### `instrument_meta` — decisiones sobre el INSTRUMENTO

| Columna | Notas |
|---|---|
| `ticker` | PK |
| `ter` | gastos corrientes anuales, en % (0–10). Lo teclea la persona: la fuente de precios no lo publica. |
| `target` | peso objetivo en % (0–100) |

Tabla aparte y no columnas en `movements` **a propósito**: son propiedades del
instrumento, no de la operación. Si vivieran en el movimiento, dos compras del
mismo fondo podrían declarar comisiones distintas y la cartera no sabría cuál
creer. Sobreviven a vender la posición entera.

### Los tres movimientos, y qué toca cada uno

| | Cantidad | Coste | Dónde cae |
|---|---|---|---|
| `buy` | sube | sube | `invested` |
| `sell` | baja | baja | `realized` **y** `realized_fifo` |
| `div` | — | — | `income`, columna propia |

Un dividendo metido en el coste bajaría el precio medio (convención fiscal de
otro país); metido en el realizado se mezclaría con plusvalías, que tributan por
su propia regla.

---

## 3. Arquitectura

Dos núcleos **puros** (sin red, sin disco, sin reloj) y una capa web que tiene
todos los sockets del proyecto.

```
cartera/parsing.py    261 líneas · texto -> dato
cartera/positions.py  247 líneas · movimientos -> posiciones valoradas
cartera/returns.py               · rentabilidad, riesgo, diversificación, rebalanceo
cartera/plan.py                  · aportaciones, objetivo propio y reglas de atención
cartera/fiscal.py                · simulador de venta: FIFO, tramos, compensación
cartera/exposure.py              · divisa por cotización y por transparencia
cartera/splits.py                · detección y ajuste de splits
dashboard.py                     · Flask: rutas, cachés, límite de tasa, CSRF, SQLite, adaptadores Yahoo
geo.py                           · transparencia por país de fondos y ETFs
backup_cartera.py     274 líneas · copia en línea de SQLite + rotación
alertas_cartera.py    217 líneas · aviso diario de cambio de zona
static/cartera.js    1031 líneas · toda la pantalla
static/cartera-map.js 335 líneas · mapa de exposición (módulo aparte, se entera por eventos)
templates/cartera.html 264 líneas
```

**`positions.compute(movs, market)` recibe el mercado por parámetro**: un objeto
con seis métodos (`warm`, `currency`, `base_factor`, `fx_series`, `fx_now`,
`last_price`). Por eso la regla de que un coste medio usa el cambio del día de
la compra se puede comprobar sin tocar Yahoo.

---

## 4. Rutas

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/cartera` | la página |
| GET | `/api/cartera` | libro + posiciones + resumen |
| POST | `/api/cartera` | alta de un movimiento |
| PATCH | `/api/cartera/<id>` | corregir un movimiento |
| DELETE | `/api/cartera/<id>` | borrar uno |
| POST | `/api/cartera/clear` | vaciar (con confirmación en el cliente) |
| POST | `/api/cartera/upload` | importar CSV/Excel |
| GET | `/api/cartera/export` | CSV **reimportable** |
| GET | `/api/cartera/history` | serie de valor + índice de referencia; `?range=`, `?from=`, `?to=` |
| GET | `/api/cartera/rendimiento` | TWR, TIR, caída, volatilidad, Sharpe; mismos parámetros de ventana |
| GET | `/api/cartera/correlacion` | matriz y diversificación real |
| GET | `/api/cartera/zonas` | zona del índice por posición abierta |
| GET | `/api/cartera/geo` | exposición por país, con transparencia |
| GET | `/api/cartera/rebalanceo` | desvío vs objetivo y reparto de una aportación |
| POST | `/api/cartera/ter` | declarar gastos corrientes |
| POST | `/api/cartera/objetivo` | declarar peso objetivo |
| GET | `/api/instrumento` | divisa y último precio de un símbolo |
| GET | `/api/search` | buscador por nombre, ticker o ISIN |
| GET | `/api/cartera/estado` | bloque ejecutivo, cobertura y avisos |
| GET | `/api/cartera/aportaciones` | calendario mensual de flujos |
| POST | `/api/cartera/plan` | objetivo propio: capital, mensual, horizonte |
| GET | `/api/cartera/simular-venta` | «si vendo X»: FIFO exacto + impuesto estimado |
| GET | `/api/cartera/divisa` | exposición por divisa, dos lecturas |
| GET/POST | `/api/cartera/splits` | splits pendientes; aplicar o marcar resuelto |

Todo lo que muta pasa por un guard CSRF **global** (`before_request`), no por un
decorador por ruta: una ruta nueva nace protegida. Lo mismo el guard de
publicación: cubre `/cartera` y `/api/cartera*` por prefijo.

---

## 5. La pantalla, en orden

1. **Resumen** — invertido, valor de mercado, P&L no realizado, P&L realizado
   (con interruptor Medio/FIFO), dividendos cobrados, rentabilidad total.
2. **Evolución** — cartera vs capital aportado vs un índice a elegir, con
   **selector de temporalidad** (1M · 3M · 6M · YTD · 1A · 5A · Todo · fechas).
3. **Rentabilidad** — TWR, TIR, acumulado; la frase que sale de restarlos;
   caída máxima, caída actual, volatilidad y Sharpe; desglose por año; y la
   comparación con el índice.
4. **Posiciones** — 12 columnas: activo, **zona**, **peso**, cantidad, coste
   medio, invertido, último, valor, P&L no realizado, %, **dividendos**, P&L
   realizado.
5. **Quién ha puesto el dinero** — contribución al resultado en euros, ordenada.
6. **Rebalanceo** — desvío vs objetivo y reparto de una aportación.
7. **Qué te cuesta** — comisiones pagadas, retenciones, gastos corrientes y su
   arrastre a 10 y 20 años.
8. **El activo y la divisa** — reparto exacto (se oculta si todo está en euros).
9. **Diversificación real** — «contando líneas» → «apuestas reales», matriz.
10. **Exposición geográfica** — mapa por transparencia: abre cada fondo y mira
    qué país hay dentro, no dónde está domiciliado.
11. **Alta manual · Importar/exportar · Movimientos**.

---

## 6. INVARIANTES — lo que no se puede romper

Cada uno de estos nació de un defecto que producía **un número equivocado**, no
un error visible. Es la parte más importante de este documento.

### 6.1 Valoración

- **El coste usa el cambio del DÍA DE LA COMPRA**, nunca el de hoy. Valorar el
  coste al cambio actual convierte una posición sana en una pérdida imaginaria.
- **`fx_now` y `fx_series` devuelven euros por unidad COTIZADA**, con el factor
  de divisa ya aplicado (GBp llega como GBPEUR × 0,01). `base_factor` no se
  reaplica: hacerlo dividiría la posición entre cien y seguiría pareciendo un
  número razonable.
- **Una posición sólo se reporta en euros si se conocen su divisa Y su tipo.**
  Si falta cualquiera, los campos en euros salen a `null` con un `why`. Nunca
  un número fabricado suponiendo cambio 1,0.
- **La columna en euros es todo o nada**: `invested`, `market_value` y `unreal`
  están los tres o no está ninguno. Una fila mezclada se lee como una pérdida.
- **Una venta no puede dejar la posición en negativo**: eso es un error de
  datos, y se reporta como `oversold` en vez de inventar títulos negativos.
- **Los movimientos sin fecha ordenan los ÚLTIMOS.** La cadena vacía va antes
  que cualquier fecha real, y eso convertía un movimiento sin fecha en el más
  antiguo, reseteando el precio medio en silencio.
- **Un peso `null` no es un peso 0.** Cero dice «no tienes casi nada de esto»;
  la verdad es «no he podido calcular cuánto tienes».

### 6.2 Rentabilidad

- **El dividendo entra en el TWR como RETIRADA.** Un reparto tira el precio sin
  que se pierda nada: `(95 − (−5)) / 100 = 0 %`. Sin eso, cada cobro restaba.
- **La serie de precios es cierre CRUDO, no ajustado** (el endpoint se pide sin
  `events=` y se lee `close`, no `adjclose`). Por eso tratar los dividendos
  aparte es correcto. **Si alguien cambia a `adjclose`, los dividendos pasarían
  a contarse DOS VECES.**
- **La comparación con el índice usa el TWR SIN dividendos** de la cartera,
  porque la serie del índice tampoco lleva los suyos. Enfrentar *total return*
  contra *price return* regala ~1,5 % anual.
- **Todo cálculo va a resolución DIARIA.** El gráfico submuestrea a ~800 puntos
  para pintar; encadenar tramos de uno de cada cuatro días coloca los flujos en
  el día que no es.
- **Por debajo de un año no se anualiza NADA**, TIR incluida (la TIR ya *es* una
  tasa anual). Una sola bandera gobierna las dos cifras.
- **XIRR por bisección, no Newton-Raphson.** Newton diverge o cae en una raíz
  absurda con aportaciones pequeñas y frecuentes, que es la cartera típica. El
  techo se escala 10 → 100 → 1e4 → 1e6.
- **Sin cambio de signo, la TIR es `null`**, no un cero.

### 6.3 Ventanas temporales

- **Recortar no es una ventana: hay que RESEMBRAR.** El índice se compra de
  nuevo el primer día del tramo con el valor que la cartera tenía ese día, y
  recibe sólo los flujos de dentro. «Invertido» pasa a ser el capital que ya
  estaba más lo aportado dentro. Las tres líneas parten del mismo punto.
- **El primer punto es el cierre ESTRICTAMENTE ANTERIOR** al inicio del tramo
  (`searchsorted(ini, side="left") − 1`). Para medir enero hace falta el cierre
  del 31 de diciembre. Con `side="right"` y una fecha que cae en día hábil, el
  corte se planta encima y el primer día se queda sin referencia.
- **En la TIR de una ventana, el capital del primer día entra como compra de ese
  día.** Sin eso hay cobros sin salida que los pague y la tasa se dispara.
- **`ytd` no son 365 días**: es «desde el 1 de enero». Las fechas se resuelven
  contra el último día de la SERIE, no contra el reloj.

### 6.4 Riesgo

- **La caída máxima NO se mide sobre los euros.** Un ingreso sube el saldo, y
  aportar no es recuperarse: una transferencia puede devolver la cifra a su
  máximo anterior y dar por superada una caída de la que el mercado no ha
  vuelto. Con aportación mensual, una bajada larga puede no verse nunca. Se
  encadenan los mismos factores del TWR en un índice de rendimiento
  (`nav_series`) y la caída se mide sobre eso. **Mismo argumento para la
  volatilidad.**
- **Un tramo no medible deja la serie PLANA**, no en 0 %: no se sabe qué pasó, y
  eso no es lo mismo que decir que no pasó nada. Los tramos excluidos se
  **cuentan** y se pueden enseñar.
- **El Sharpe lleva el tipo sin riesgo explícito** (hoy 0 %) escrito en
  pantalla: un Sharpe sin decir contra qué se calcula no se compara con nada.

### 6.5 Diversificación

```
por pesos          1 / SUM(w_i²)
por correlación    1 / SUM_ij(w_i · w_j · rho_ij)
```

La segunda generaliza a la primera (con la identidad devuelve la primera; con
todo a 1 devuelve 1,0). Contar líneas dice «5» tanto para cinco apuestas
distintas como para cinco fondos del mismo índice.

- **Correlación sobre rendimientos EN EUROS**, no en divisa nativa: dos activos
  que no se parecen se mueven juntos para quien mide en euros por el mero hecho
  de cotizar los dos en dólares.
- **Calendario COMÚN por intersección, nunca rellenado.** Inventar sesiones que
  un fondo no tuvo fabrica correlación.
- **Ventana de un año.** Con diez, dos activos que hace tiempo no se parecen
  salen juntos por lo que hicieron en 2020.
- **Con menos de 60 sesiones en común no se publica correlación**: es ruido con
  dos decimales.
- **Los pesos se renormalizan** sobre lo que entra en la matriz.

### 6.6 Importación / exportación

- **Lo exportado tiene que reimportarse sin cambiar nada.** La cabecera es
  exactamente el juego de nombres que detecta el importador.
- **`kind` NO se exporta**: se deriva del símbolo en cada lectura para que una
  etiqueta vieja no re-etiquete una posición.
- **Cantidades con `repr(float)`**: viaje de ida y vuelta exacto. Redondear no
  se ve en pantalla y falsea el precio medio al reimportar.
- **Palabras completas** (`compra`/`venta`/`dividendo`), nunca códigos de una
  letra: una `c`/`v`/`d` está a un dedazo en Excel de invertir una operación.
- **`utf-8-sig`**: sin BOM, Excel lee UTF-8 como latin-1 y destroza los acentos.
- **Todo valor que el sistema emite tiene que releerse como sí mismo.** `div`
  era el código canónico que guardaba la base y no estaba en el vocabulario del
  parser: el dividendo volvía a entrar **como compra**.
- **La renta gana al desempatar el lado.** Un extracto español escribe «Abono
  dividendo», y `abono` a solas sí es una venta.
- **El tipo sale del SÍMBOLO, no de lo que diga la fuente**: `0P…` es un fondo
  no cotizado; `<ISIN>.<MIC>` es una línea de bolsa, así que no puede ser un
  fondo no cotizado.
- **Reimportar el mismo fichero no duplica**: se detectan repetidos y se dicen.

### 6.7 Splits

- **La serie de precios viene ajustada por splits**, así que sólo cuadra con la
  cantidad de títulos POSTERIOR al split. Si el libro está en la escala vieja,
  el valor de hoy y todo el histórico salen divididos por el factor, sin error.
- **El programa NO puede saber si ya se tuvo en cuenta**: «10 títulos» es el
  mismo número a los dos lados del split. Por eso detecta y espera, en vez de
  decidir. Cerrar el aviso es una decisión de quien tiene el libro.
- **El ajuste es seguro porque NO mueve el coste**: multiplica cantidad y divide
  precio por el mismo factor. Esa igualdad se comprueba antes de escribir, y si
  falla no se toca una sola fila.
- **Se saca copia de seguridad antes de reescribir.** Es la única operación del
  panel que modifica movimientos ya apuntados.

### 6.8 Coherencia del gráfico

- **`portfolio` e `invested` describen SIEMPRE el mismo conjunto de
  posiciones.** Un instrumento sin serie se cae de las dos, y se nombra en
  `excluded`. Dejarlo en una sola es lo que hacía que el gráfico discrepara de
  la tabla por el coste entero de lo excluido.
- **Un histórico prestado (`proxy`) se revalida en cada ejecución** —misma
  divisa, desvío de precio bajo un umbral— y se dice cuál se usó y por qué se
  rechazó otro. Un instrumento equivocado dibujado en silencio es peor que no
  dibujar nada.

---

## 7. Lo que ya está descartado con medición

No volver a proponer esto sin evidencia nueva:

| Idea | Por qué no |
|---|---|
| **Fibonacci** en las zonas de precio | El nivel varía 36–56 % del precio según el swing elegido; 25 niveles cubren el 40 % del espacio de precios (no es falsable); p ≈ 0,28 frente al azar. |
| **Más indicadores** (MACD, %B, estocástico, ROC) | Correlación 0,61–0,92 con componentes que ya están: no añaden dimensión. |
| **Lógica de timing** (comprar en Capitulación, vender en Euforia) | Backtest en S&P 500 y BTC: *buy & hold* gana; el corto añade riesgo sin retorno. |
| **Rebalancear vendiendo** | Cada venta con plusvalía es hecho imponible en España. Se rebalancea comprando. |

---

## 8. Huecos conocidos y candidatos

Ordenados por lo que aportarían a quien tiene dinero dentro.

1. **Aportación periódica / calendario de flujos.** No hay vista de «cuánto metí
   y cuándo». La serie de flujos ya está calculada.
2. **Simulador fiscal «si vendo X»**. El FIFO ya está; falta la proyección de la
   plusvalía y la compensación de minusvalías.
3. **Beta y correlación contra el índice**, no sólo entre posiciones.
4. **Divisa agregada**: el reparto activo/divisa es por posición; falta la
   exposición neta por divisa de toda la cartera.
5. ~~**Splits.**~~ **RESUELTO 2026-08-19.** Y con una corrección a lo que este
   documento decía: afirmaba que un split sin aplicar descuadra «el histórico,
   no el valor de hoy». **Es falso.** El precio actual sale de la cotización en
   vivo, ya post-split, así que la cantidad vieja del libro rompe también el
   valor de HOY. Medido con datos reales: 10 NVDA compradas antes del 10:1 de
   junio de 2024 se valoraban en 1.891 € contra 3.633 € invertidos —una pérdida
   del 48%— cuando en realidad eran 100 títulos y 18.919 €. Ahora se detecta,
   se avisa y se puede ajustar. Ver `cartera/splits.py`.
6. **Instrumentos sin histórico.** Los `<ISIN>.<MIC>` que la fuente puntúa pero
   no grafica quedan fuera de zona, correlación y gráfico. Se dice en pantalla,
   pero no hay alternativa.
7. **Sin autenticación.** El guard de publicación cierra un fallo de
   CONFIGURACIÓN, no a un atacante que ya cruzó el proxy.
8. **Sin linter** en el CI.
9. **TER a mano.** No se lee de ninguna fuente; si se olvida, la cobertura lo
   dice pero el coste real queda infravalorado.

---

## 9. Cómo se comprueba

471 tests herméticos: toda llamada saliente está simulada. El CI corre en
3.11 / 3.12 / 3.14 y repite la suite con un **guardia de red** activo
(`MZ_NO_NETWORK=1`, en `conftest.py`) que intercepta `connect`, `connect_ex`,
`create_connection` y `getaddrinfo`.

| Fichero | Tests | Cubre |
|---|---:|---|
| `tests/test_returns.py` | 42 | TWR, TIR, caída, volatilidad, Sharpe, N efectivo, rebalanceo |
| `tests/test_cartera_rendimiento.py` | 28 | ventanas, resembrado, costes, correlación, contribución |
| `tests/test_cartera.py` | 26 | contabilidad y vocabulario |
| `tests/test_positions_domain.py` | 25 | la aritmética del dinero, sin Flask ni red |
| `tests/test_cartera_dividendos_edicion.py` | 22 | dividendos, corrección, zonas, divisa |
| `tests/test_geo.py` | 18 | transparencia por país |
| `tests/test_cartera_backup.py` | 16 | copia, verificación, rotación |
| `tests/test_alertas_cartera.py` | 13 | cuándo avisa y, sobre todo, cuándo calla |
| `tests/test_instrument_name.py` | 9 | identidad del instrumento |
| `tests/test_cartera_guard.py` | 7 | el libro no se sirve sin declarar login |
| `tests/test_history_proxy.py` | 7 | histórico prestado |

**Verificaciones externas, que valen más que un test propio:**

- La TIR reproduce el ejemplo publicado de la función `XIRR` de Excel con
  **desvío 1,5e-9**.
- El reparto activo/divisa **suma el resultado no realizado al céntimo**.
- El producto de los años reconstruye el TWR total.
- Al extraer la reconstrucción del gráfico, se comprobó que los puntos
  existentes salían **idénticos** antes y después.

---

## 10. Operación

| Pieza | Cuándo | Qué hace |
|---|---|---|
| `market-zones.service` | siempre | el panel (loopback) |
| `market-zones-cartera-backup.timer` | 05:15 UTC | copia en línea de SQLite, verificada, 14 estados **distintos** |
| `market-zones-alertas.timer` | 23:30 UTC | avisa al entrar o salir de una zona extrema |

- La copia usa `Connection.backup()`, **no `cp`**: la base está viva y un `cp` a
  mitad de escritura da un fichero que abre sin quejarse y al que le faltan
  filas.
- La alerta **siembra en silencio la primera vez**. Un aviso que no corresponde
  a un cambio enseña a ignorar los avisos.
- Sólo habla de transiciones que tocan Capitulación o Euforia: Equilibrio y
  Precaución es donde vive un activo la mayor parte del tiempo.
- **`PrivateNetwork=true` NO se aplica en unidades de usuario** (el journal lo
  avisa). La garantía real es que el script de copia sólo importa `argparse`,
  `hashlib`, `os`, `sqlite3`, `sys` y `time`.

---

## 11. Criterios para juzgar una propuesta

En orden de peso:

1. **¿Puede producir un número equivocado en vez de un error?** Es la clase de
   fallo que este apartado ha tenido una y otra vez, y la que más caro sale.
2. **¿Se puede comprobar contra algo externo** —una fórmula publicada, una
   identidad que tenga que cumplirse, un resultado de una hoja de cálculo—
   o sólo contra sí misma?
3. **¿Dice lo que NO cubre?** Una media sobre el 60 % del capital que se
   presenta como si cubriera el 100 % es peor que no dar el número: parece
   medido.
4. **¿Añade una dimensión nueva o repite una que ya está?** El estudio de
   redundancia rechazó cuatro indicadores por correlacionar 0,61–0,92 con lo
   que ya había.
5. **¿Responde una pregunta que alguien con dinero dentro se hace de verdad?**
