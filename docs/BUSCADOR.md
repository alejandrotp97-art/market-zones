# Buscador de ETFs y fondos europeos

> **Fase 1 construida el 2026-08-24. Fase 2 sin fuente todavía.** Cada cifra de
> aquí viene de una sonda ejecutada contra la fuente real, no de documentación ni
> de memoria. Donde una medición contradijo al diseño, manda la medición y se
> dice cuál era el error. **No contiene ni un dato de la cartera real.**

---

## 1. El problema, dicho con precisión

El buscador actual (`_yahoo_search`, `dashboard.py:922`) **no es un buscador**:
es un resolvedor de tickers. Sirve para «sé lo que quiero, dame su símbolo». No
sirve para «enséñame qué hay».

Y no es un defecto de implementación, es un techo de la fuente:

| Medición sobre `query2.finance.yahoo.com/v1/finance/search` | Resultado |
|---|---|
| `quotesCount=7 / 10 / 25 / 50` | **7, 7, 7, 7** — tope duro |
| `quoteSummary` (TER, categoría) | **HTTP 401** sin crumb |
| «S&P 500 UCITS acumulacion» | **0 resultados** — no hay búsqueda semántica |
| «Vanguard 20+ Year Euro Treasury» | **0 resultados** — y es un fondo que la app ya valora |
| `quoteType` del mismo ETF | `ETF` en XETRA, `MUTUALFUND` en Stuttgart |

Sobre siete resultados no se construye un explorador. Y la última fila confirma
lo que ya sabíamos por otro camino: **el tipo sale del símbolo, no de Yahoo.**

---

## 2. La fuente: ESMA FIRDS

El registro oficial de la UE de instrumentos admitidos a negociación. Gratis,
sin clave, sin ToS que lo impida.

**Descubrimiento de ficheros** (Solr):

```
https://registers.esma.europa.eu/solr/esma_registers_firds_files/select
    ?q=*&fq=file_type:FULINS&wt=json&rows=20&sort=publication_date+desc
```

Los ficheros van partidos por la **primera letra del CFI**. Sólo hacen falta dos:

| Fichero | Contenido | Tamaño |
|---|---|---|
| `FULINS_C` | Vehículos de inversión colectiva — **ETFs y fondos cotizados** | **3,7 MB** (91 MB de XML) |
| `FULINS_D` | Deuda — donde viven los **ETC** (el oro físico, entre otros) | ~52 MB en 4 partes |

Publicación **semanal**. Namespace `auth.017.001.02`. **`FULINS_C` se parsea
entero en 2,1 s** con `iterparse` y `el.clear()`.

### Lo que trae cada registro

```xml
<RefData>
  <FinInstrmGnlAttrbts>
    <Id>IE00B4L5Y983</Id>                          <!-- ISIN -->
    <FullNm>ISHARES CORE MSCI WORLD UCITS ETF</FullNm>
    <ClssfctnTp>CEOGES</ClssfctnTp>                <!-- CFI ISO 10962 -->
    <NtnlCcy>USD</NtnlCcy>              <!-- divisa del FONDO, no de la plaza -->
  </FinInstrmGnlAttrbts>
  <Issr>549300MXTUQPFTZCTX23</Issr>                <!-- LEI del emisor -->
  <TradgVnRltdAttrbts>
    <Id>XETR</Id>                                  <!-- MIC de la plaza -->
    <FrstTradDt>2009-10-01T…</FrstTradDt>
  </TradgVnRltdAttrbts>
</RefData>
```

### El censo, medido

| | |
|---|---|
| Listados en `FULINS_C` | **150.809** |
| ISIN únicos | **18.376** |
| `CE` = ETF | **7.879** |
| `CI` = fondo cotizado | **7.762** |
| Líneas por ETF | mediana **7**, máximo **48** |
| Domicilio IE + LU | **56,6 %** de los ETF |

**Ese «mediana 7, máximo 48» es el problema real del universo europeo**, y es
justo lo que Yahoo no sabe contar: no buscas un ticker, buscas un **fondo**, y
después eliges dónde comprarlo.

### El CFI regala la política de reparto

Cuarta letra del CFI: **`G` = acumulación, `I` = distribución, `J` = mixta.**

Verificado 5 de 5 contra instrumentos conocidos:

| ISIN | CFI | Lectura | ¿Correcto? |
|---|---|---|---|
| `IE00B4L5Y983` iShares Core MSCI World | `CE**O G**ES` | acumula | ✔ |
| `IE00BK5BQT80` Vanguard FTSE All-World | `CE**O G**ES` | acumula | ✔ |
| `IE00B5BMR087` iShares Core S&P 500 | `CE**O G**ES` | acumula | ✔ |
| `IE00B4L5YC18` iShares MSCI EM | `CE**O G**ES` | acumula | ✔ |
| `IE00B3RBWM25` Vanguard FTSE All-World | `CE**O I**ES` | **distribuye** | ✔ |

---

## 3. Lo que FIRDS NO da, y por qué no se va a fingir

### No hay bandera UCITS

FIRDS registra instrumentos negociables, no regímenes regulatorios. Y **filtrar
por la palabra «UCITS» en el nombre sería un error medible**:

| | «UCITS» aparece en el nombre |
|---|---|
| ETF (`CE`) | **27,9 %** |
| Fondos (`CI`) | **0,8 %** |

Un filtro así escondería **dos tercios del universo real**. La aplicación no
puede poner una etiqueta que no puede sostener.

**Lo que sí se puede decir con la fuente en la mano** es el **domicilio**, que
sale del prefijo del ISIN (`IE`, `LU`, `FR`, `DE`…) y es lo que de verdad manda
en la fiscalidad y la retención en origen. Se enseña como domicilio y se llama
domicilio. Ni una palabra sobre UCITS que no venga escrita en el nombre.

### No hay TER

Sigue siendo manual, y la app ya lo hace bien: `instrument_meta` lo guarda y
«Qué merece tu atención» avisa de cuántas posiciones no lo tienen declarado y de
dónde sacarlo (el KID/DFI). Eso no cambia. Un TER inventado sería peor que un
TER ausente, porque la tarjeta de costes ya calcula sobre lo declarado y **dice
qué porcentaje del patrimonio cubre**.

Tampoco hay: método de réplica, patrimonio bajo gestión, ni índice replicado.

---

## 4. El hallazgo que parte el diseño en dos fases

Se comprobó la cobertura contra las posiciones abiertas reales. De **5**
posiciones, `FULINS_C` cubre **1**:

- **3 son símbolos `0P…`** — identificadores de Morningstar, es decir **fondos
  NO cotizados**. FIRDS sólo registra lo admitido a negociación en un mercado.
  No están, y no van a estar.
- **1 es un ETC** — que es deuda, y vive en `FULINS_D`, no en `_C`.
- **1 es un ETF** y sale entero: tipo, acumulación y sus 39 plazas.

**Un catálogo sólo-FIRDS sería un buscador excelente de ETFs que no encuentra
los fondos que el usuario realmente tiene.** De ahí las dos fases.

Y Yahoo tampoco rescata esa mitad: buscar «Vanguard 20+ Year Euro Treasury» —un
fondo que la app ya valora todos los días— devuelve **cero resultados**. Sólo
responde al identificador exacto `0P0000CV2T`. Su índice de fondos es
irregular: «Fidelity MSCI World Index» sí devuelve dos.

---

## 5. Fase 1 — ETFs y fondos cotizados (construida)

Los **ETC se quedaron fuera** y el motivo está en el apartado 9: son deuda, viven
en otro fichero, y el filtro para reconocerlos ahí no está comprobado.

### 5.1 Catálogo local

Base propia, **separada de `cartera.db` y fuera del directorio del proyecto**:
son datos públicos y reconstruibles, y mezclarlos con el libro convertiría cada
copia de seguridad de la cartera en una copia de 18.000 filas ajenas.

Que viva fuera no es orden: el proceso de ingesta necesita escribir donde esté el
catálogo, y si eso fuera el directorio del proyecto, su unidad tendría que abrir
en escritura la carpeta donde está `cartera.db`. **Medido en esta máquina: en una
unidad de USUARIO el aislamiento de rutas del home no se aplica** — ni
`ProtectHome=read-only` ni un `ReadOnlyPaths` anidado dentro de un
`ReadWritePaths` impiden la escritura, comprobado contra un fichero señuelo.
`ProtectSystem=strict` sí funciona. Así que la única separación que se sostiene
es no compartir carpeta.

```
~/.local/state/market-zones/instruments.db      # FUERA del repositorio
  funds(isin PK, name, cfi, kind, dist_policy, domicile, issuer_lei, updated)
  listings(isin, mic, ccy, first_trade, yahoo_symbol, PRIMARY KEY(isin, mic))
  meta(source, file_name, published, checksum, ingested)   -- procedencia
```

`meta` no es adorno: la pantalla tiene que poder decir **«registro UE, datos del
22-ago»**. Un catálogo sin fecha visible es un catálogo en el que hay que
confiar a ciegas.

### 5.2 Ingesta

Script aparte (`ingest_firds.py`), timer semanal como los que ya existen para la
copia y las alertas. Descarga → parsea con `iterparse` → escribe en una tabla
temporal → intercambio atómico. Nunca deja el catálogo a medias.

**Sin red en los tests**: el `conftest.py` ya bloquea `connect`/`getaddrinfo`
con `MZ_NO_NETWORK=1`. La ingesta se prueba contra un XML recortado de tres
registros, en fichero.

### 5.3 El puente a los precios — **el diseño estaba equivocado**

Este apartado proponía una tabla MIC → sufijo de Yahoo, verificada plaza a plaza,
y la señalaba como la parte frágil. **Se midió y no hace falta.**

Lo que se comprobó, en este orden:

1. `<ISIN>.<sufijo>` **no funciona como símbolo genérico**: veinte sufijos
   probados contra `IE00B4L5Y983`, veinte respuestas 404. Yahoo sólo acuña
   símbolos con forma de ISIN para algunas líneas sueltas.
2. Pero **resolver por ISIN a través del buscador de Yahoo sí funciona**: 18 de
   18 ISIN tomados al azar del catálogo devolvieron un símbolo que cotiza **y**
   que tiene serie histórica utilizable.

Así que la resolución es **perezosa y verificada**, no una tabla:

  * El catálogo contesta **qué existe** — sin red, al teclear.
  * Al ELEGIR un fondo, y sólo entonces, se pregunta a Yahoo con qué símbolo lo
    conoce. Una llamada por elección, no por tecla.
  * Se devuelve **con divisa y con si tiene histórico**, y no se acepta nada a
    ciegas: sin cotización el fondo **no se da por elegido**, porque apuntar un
    movimiento que la cartera no sabrá valorar es un fallo que aparece semanas
    después.

**Por qué importa la divisa aquí:** el primer ISIN de la muestra resolvió a
`CBUS5AN.MX` — la línea **mexicana, en pesos**, de un ETF luxemburgués. Aceptar
eso en silencio es exactamente lo que ya partió una posición en dos.

### 5.3 bis — la divisa NO es de la plaza

El diseño prometía filtrar por «divisa disponible». **Es falso, y la fuente lo
desmiente:** de los 18.376 ISIN del fichero, **cero** tienen más de una divisa
entre sus plazas. El `NtnlCcy` de FIRDS es la moneda en que está **denominado el
fondo**, no en la que se cruza en cada mercado.

Consecuencia en el modelo: `ccy` cuelga de `funds`, no de `listings`. En qué
divisa comprarías en Milán no lo dice el registro, y se sabe al pedir el precio.

### 5.4 La búsqueda

Local, sobre SQLite **FTS5**. Sin red, sin espera, sin tope de siete.

Se busca por **fondo**, no por línea. Un resultado es un ISIN con sus datos, y
las plazas van dentro, plegadas:

```
BUSCAR: "msci world"                    [ETF] [Fondo]  [acumula] [distribuye]
                                        [domicilio: IE · LU · todos]
─────────────────────────────────────────────────────────────────────────
iShares Core MSCI World UCITS ETF                          IE00B4L5Y983
ETF · acumula · Irlanda · 12 plazas                    registro UE, 22-ago
  └─ XETRA EUR · Milán EUR · Ámsterdam EUR · Londres USD  ▾
```

Filtros que la fuente **sí** sostiene: tipo (ETF/fondo), reparto
(acumula/distribuye), domicilio, divisa disponible, plaza. Ninguno inventado.

### 5.5 Dónde entra en la app

En `#f-ticker` del formulario de alta, que ya tiene desplegable, teclado y
selección. **No es una pantalla nueva**: es la misma casilla, con un índice
detrás que sabe contestar.

Cuando eliges la plaza, se rellena el símbolo de Yahoo y `name`/`kind` salen del
catálogo — que es una fuente mejor que la heurística actual sobre el símbolo.

Yahoo **se queda** como respaldo: acciones sueltas, cripto, y todo lo que el
catálogo no cubra. El resultado dice de dónde viene. Nunca se mezclan dos
procedencias sin etiquetarlas.

---

## 6. Fase 2 — fondos no cotizados

> Construida el 2026-08-24, y **la premisa de partida era falsa**. Se daba por
> hecho que los fondos no cotizados eran una mitad entera que faltaba y que hacía
> falta una fuente nueva. Midiendo, resultó que la Fase 1 ya alcanzaba a buena
> parte de ellos y que el hueco de verdad es mucho más pequeño — y no tiene
> fuente libre.

### 6.1 Las fuentes que se probaron, y por qué no valen

| Fuente | Resultado |
|---|---|
| **CNMV**, registro de IIC | Hay descarga masiva por año, pero detrás de *postbacks* de ASPX: ningún enlace a fichero en el HTML. Y la «información individual de IIC» es contabilidad de fondos españoles, no un registro de UCITS extranjeras con ISIN. |
| **datos.gob.es** | La CNMV no publica ahí sus registros de IIC. |
| **API de Vanguard** (`vanguardinvestor.co.uk`) | Contesta JSON con 181 productos, **115 no cotizados**, nombres y hasta el OCF… y **sin ISIN**. `ids` viene vacío. Además son clases británicas. Inservible para unir con nada. |
| **Yahoo, por nombre** | **0 de 18**. Ni uno de los 18 fondos no cotizados de Vanguard probados devuelve resultado. Yahoo *sabe* ponerles precio por su `0P…`, pero **no los indexa por nombre**. |
| **Morningstar** | ✅ **La tiene, y completa.** Ver 6.5 — se probó DESPUÉS de dar por perdida la fase, a instancias del usuario. |
| **Finect** | Sin API pública: 404 en las dos rutas probadas. |

### 6.2 El hallazgo que cambió la fase

Se comprobó contra las tres posiciones `0P…` reales del usuario:

- **`IE00BYX5NX33`** (Fidelity MSCI World Index Fund - P EUR ACC) **ya estaba en
  el catálogo**, con tres plazas. Fidelity lista sus fondos de inversión en
  mercados alemanes, así que **FIRDS los recoge**.
- Los de **Vanguard** no. Nunca se admiten a negociación en ningún mercado.

Y el catálogo tiene **7.762 instrumentos de tipo `Fondo`**. Sobre una muestra de
14 al azar: **12 resuelven a una cotización** y **10 tienen línea en euros** —
varios a través de su identificador de Morningstar (`0P…`), que es exactamente
el mismo que ya usaba la cartera.

**El fondo del usuario resuelve a `0P0001CLDK.F`: el ticker que ya tenía
apuntado.** La unión catálogo → ISIN → Yahoo → `0P…` funciona.

Así que la Fase 2 no era «traer una mitad que falta»: era **hacer visible una
mitad que ya estaba**, y **decir la verdad sobre el trozo que de verdad falta**.

### 6.3 Lo que se construyó

**Cupo por tipo en la búsqueda.** Buscando «msci world index» salían **cinco ETF
y ni un fondo**, habiendo fondos que encajaban mejor. Se probaron tres fórmulas
de orden —lineal por plazas, logarítmica, y sólo-en-cotizados— y las **tres**
daban `ETF/ETF/ETF/ETF/ETF`. El problema no era el peso: hay muchísimos más ETF y
**un fondo nunca gana un cupo global**. Ahora se busca por tipo y se juntan los
resultados, y la pantalla pone un subtítulo por bloque.

Dos trampas que salieron al hacerlo, las dos con test:

- El relleno final volvía a traer ETF, que al agrupar se ponían delante y
  empujaban a los fondos fuera del corte. **Un cupo no sirve de nada si luego se
  rellena con lo mismo que estaba limitando.**
- Con `limit=20` el cupo salía a 6 y el primer fondo caía por debajo del pliegue
  del desplegable. Para el caso, no estar. Se bajó a 12.

**Y se dice qué no está.** Cuando el registro no encuentra nada, la pantalla
explica que sólo recoge lo admitido a negociación en la UE, y que un fondo
contratado directamente con la gestora se apunta **pegando su ISIN o su `0P…`**.
Es la diferencia entre «no existe» y «no está en esta lista».

### 6.5 Morningstar — la fuente que sí existía

Se dio la fase por cerrada diciendo «no hay fuente libre» **tras probar cuatro**.
El usuario preguntó por Morningstar y Finect. Finect no tiene API pública;
Morningstar **sí**, y resuelve el problema entero:

```
https://lt.morningstar.com/api/rest.svc/klr5zyak8x/security/screener
    ?universeIds=FOESP$$ALL&term=<texto>&languageId=es-ES&currencyId=EUR
    &securityDataPoints=SecId|Name|isin|PerformanceId|OngoingCharge|categoryName
```

`FOESP$$ALL` son los fondos **registrados para su venta en España**: **49.679**
medidos el 2026-08-24. Y devuelve las tres cosas que faltaban:

| Campo | Por qué importa |
|---|---|
| `isin` | El identificador universal |
| **`PerformanceId`** | **Es el `0P…` con el que Yahoo ya sabe pedir precios.** Sin esto no hay forma de valorarlos |
| `OngoingCharge` | **Los gastos corrientes**, que el registro de la UE no publica |

Las **tres** posiciones `0P…` del usuario salen con su identificador exacto, y
también Bestinver, Cobas y azValor. Comprobado que los tres resuelven a un
símbolo que cotiza y tiene serie.

**Se consulta, no se copia.** La clave `klr5zyak8x` es la que Morningstar
incrusta en sus buscadores públicos; bajarse su base entera sería otro uso del
que está expuesto. Decisión tomada a propósito por el dueño del panel:

- Se pregunta **sólo cuando el registro europeo no devuelve NI UN fondo**.
  Verificado en navegador: buscando «fidelity msci world index» —que sí está en
  el registro— salen **cero** peticiones externas.
- **No se guarda nada.** Caché en memoria de 5 minutos y ya.
- **Los gastos NO se dan por buenos solos**: se enseñan al elegir para que los
  declares tú en la ficha del instrumento. Vienen de una base comercial, no del
  registro oficial, y quien los declara en su cartera es su dueño.
- Es una fuente de **apoyo**: si no contesta, la búsqueda sigue con el registro
  oficial. Un buscador que revienta porque un tercero está caído es peor que uno
  que encuentra menos.

Un detalle que hay que respetar: **unos gastos a `0` son «no los publican»**, no
«es gratis» — salen así en fondos que sí cobran. Se tratan como ausentes.

### 6.6 Lo que sigue faltando, dicho claro

Los fondos que **ninguna gestora lista en ningún mercado**: los de Vanguard, y
los españoles de gestora (Bestinver, Cobas, azValor, los de un roboadvisor).
Comprobado: ninguno aparece buscándolos en el catálogo.

**Ya no falta: los cubre Morningstar** (6.5). Lo que queda es la dependencia —una
base comercial consultada en vivo— y por eso va etiquetada en pantalla y sólo se
llama cuando el registro oficial no llega.

Lección del episodio, que vale más que el código: **«no hay fuente» después de
cuatro intentos era una conclusión, no un hecho.** La había, y era la mejor de
todas.

## 7. Lo que este buscador NO va a hacer

Coherente con el resto del panel, y por los mismos motivos ya medidos:

- **No recomienda.** Ni «los mejores», ni «más populares», ni orden por
  rentabilidad pasada. Filtra por hechos del registro y ya.
- **No puntúa fondos.** El Panel de Zonas dice caro/barato de un precio; no
  opina sobre si un producto es bueno.
- **No inventa TER, réplica ni patrimonio.** Lo que no está en la fuente, no
  aparece; y donde falte, se dice que falta.
- **No dice «UCITS»** salvo que esté escrito en el nombre oficial.

---

## 8. Coste, honestamente

| | |
|---|---|
| Fase 1 | **Hecha, ETC incluidos.** `cartera/firds.py` (puro) · `ingest_firds.py` · catálogo + FTS5 en `dashboard.py` · 3 rutas · buscador de dos fuentes en `static/cartera.js` · temporizador semanal · 49 tests |
| Riesgo que se temía | La tabla MIC→sufijo. **No existe**: la resolución es perezosa y verificada (ver 5.3) |
| Catálogo real | **19.285 instrumentos** (7.879 ETF · 7.762 fondos · **909 ETC**) · 159.272 líneas · **20 MB** |
| Ingesta | **56 s**, pico de **201 MB**, 56 MB de descarga, domingos 08:20 UTC (ESMA publica los **sábados**, comprobado) |
| Coste en red al buscar | **cero** — la búsqueda es SQL local, medida en **0,1–2,5 ms** |
| Fase 2 | **Hecha.** Cupo por tipo + Morningstar bajo demanda. Las tres posiciones `0P…` del usuario salen por nombre |

---

## 9. Los ETC — hecho, y por otro camino

**El oro ya sale.** Un ETC es jurídicamente **deuda**, así que vive en
`FULINS_D`. Y ahí, al contrario que con los fondos, **no hay un CFI que los
separe**: medido sobre el fichero del 22-ago, el oro de Amundi es `DEMYRT`, los
de WisdomTree `DAZSPR`, los de Xtrackers `DAZSFR` — y los grupos `DA` y `DE`
juntos tienen **más de un millón de líneas de bonos corrientes**.

### El filtro, y por qué aquí sí se usa el nombre

`grupo CFI ∈ {DA, DE}` **Y** `ETC|ETP|ETN` en **alguna** variante del nombre.

No es incoherente con haber rechazado el filtro «UCITS» del apartado 3: es que la
situación es otra. Para un ETF existía una señal **estructural y completa** (CFI
`CE`) y el nombre sólo habría estorbado. Para un ETC no existe ninguna, y el
emisor sí escribe ETC/ETP/ETN en el nombre.

Y las dos mitades del filtro hacen falta:

| | |
|---|---|
| ISIN de deuda en el fichero | **642.598** |
| Con ETC/ETP/ETN en algún nombre | **988** |
| …y además en grupo `DA`/`DE` | **909** ← lo que entra |
| **Los 79 que se caen** | bonos de **EATON Corp** — «ETN» es su ticker, no un producto |
| Sólo lo declaran en ALGUNA plaza | **41 %** — hay que mirar todas las variantes |
| Recall contra ETC reales conocidos | **7 / 7**, incluido el oro de Amundi |

### El 66 % son apalancados, y se dicen

657 de los 988 llevan `3x`, `-3x`, `Short` o `Leveraged` en el nombre. Un
«Gestora Delta Gold 3x Daily Short» junto a un «Amundi Physical Gold» se parecen en
el nombre y **no se parecen en nada más**.

No se esconden —quien tenga uno tiene que poder apuntarlo— pero llevan una
etiqueta `apalancado`. El apalancamiento se lee del nombre porque el emisor está
obligado a ponerlo ahí; el registro no lo publica en ningún campo.

### Lo que costó en memoria

Doblar los 642.598 ISIN de deuda para quedarse con 909 **picaba en 1,47 GB**, por
encima del `MemoryMax` de la unidad. Se añadió una primera pasada que sólo
acumula el conjunto de ISIN candidatos: **pico 201 MB**, mismo resultado exacto.
La ingesta pasa de 44 s a 56 s y de 3,7 MB a 56 MB de descarga.

## 10. La divisa: el diseño volvió a estar equivocado

Se sugería «la línea en la divisa de denominación del fondo». **Mal.** Denominación
y divisa de compra no son lo mismo: el VanEck de uranio está denominado en USD y
en Stuttgart se cruza en euros.

Y adivinar cualquiera de las dos era el error de fondo. Medido con el oro:

| Símbolo | Precio | ¿Correcto? |
|---|---|---|
| `GOLD.AS` | 184,45 **USD** | sí, es la línea de Ámsterdam |
| `FR0013416716.SG` | 158,12 **EUR** | sí, es la de Stuttgart |

**Las dos cifras son ciertas y sólo una es la tuya.** Así que el resolvedor
devuelve **todas las líneas que cotizan**, sugiere la de la divisa de la cartera
—EUR— y deja elegir. Sobre las dos posiciones reales del usuario, la sugerencia
acierta el símbolo que ya tenía apuntado.

Además, una línea sin serie propia **no se marca «sin histórico» si tiene proxy
comprobado** en `HISTORY_PROXY`: el panel la dibuja perfectamente y asustar por
ello sería un aviso falso.

## 11. Lo que la Fase 1 dejó por hacer

- **El ranking es bueno, no perfecto.** `bm25 × plazas` acierta el primero en
  «ishares core msci wor», «vanguard all world» y «s&p 500 acc»; en «msci world»
  a secas pone primero al Amundi Swap (42 plazas) por delante del iShares Core
  (41). No hay señal en el registro para desempatarlos mejor: el patrimonio
  gestionado, que es lo que decidiría, no está en FIRDS.
- **El 27,3% de los fondos no tiene un nombre comercial limpio** entre sus
  variantes. Se enseña el menos malo. Buscar los encuentra igual, porque el
  índice lleva todos los alias.

---

*Mediciones del 2026-08-24. Reproducibles: las sondas están en el scratchpad de
la sesión y todas atacan endpoints públicos sin clave.*

---

## 12. Lo que corrigió la revisión acotada

Cuatro lentes sobre 3.259 líneas. Lo que encontraron y se arregló, todo
reproducido antes de aceptarlo:

| Defecto | Cómo se comprobó |
|---|---|
| **Fuga de descriptores**: la rama del cupo de `_catalogo_buscar` abría la conexión y salía por `return` sin cerrarla | 40 búsquedas sin filtro → 40 descriptores colgados. Tras el arreglo, **0** |
| **El filtro por ETF/ETC llamaba a Morningstar en cada búsqueda** y pintaba fondos dentro de una lista restringida a otro tipo | Navegador: 1 llamada por tecleo y 8 filas indebidas. Tras el arreglo, **0 y 0** |
| **`mejor_nombre` no era determinista**: `max` sobre un conjunto | Ganadores distintos según `PYTHONHASHSEED`; **225 de 18.376 ISIN empatan** en el fichero real. Desempate alfabético |
| **`?limit=abc` devolvía HTTP 500** | Reproducido contra el servicio; ahora 200 |
| **La unidad prometía proteger `cartera.db`** y el sandbox no lo hacía | Señuelo: escribió igual. El catálogo se muda fuera y el comentario dice la verdad |
| **El documento decía que el cupo bajó a 12** y el defecto del servidor seguía en 20 | Ahora `_LIMITE_POR_DEFECTO = 12` en el servidor, no sólo en la llamada del cliente |
| **El docstring del resolvedor prometía «todas las líneas»** y comprueba ocho | Corregido el docstring |
