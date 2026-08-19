# Guía de instalación, paso a paso

Para instalar el panel en tu propio ordenador. **No hace falta saber programar.**
Se trata de copiar comandos, pegarlos y pulsar Enter.

Después de cada paso te digo **qué deberías ver**. Si ves eso, vas bien. Si no,
mira la sección [Cuando algo falla](#cuando-algo-falla) al final.

> **Nota sobre copiar y pegar:** en la terminal, `Ctrl+V` a veces no funciona.
> Usa **`Ctrl+Shift+V`** (Windows y Linux) o **`Cmd+V`** (Mac).

---

## Lo que vas a conseguir

Un panel que se abre en tu navegador, funciona en tu ordenador y muestra:

| Página | Para qué |
|---|---|
| **Inicio** | Si un activo está caro o barato respecto a su propia historia |
| **Régimen** | En qué estado general está el mercado |
| **Screener** | Todos los activos a la vez, ordenables |
| **Comité** | Varias señales juntas para un mismo activo |
| **Cartera** | Tus posiciones, cuánto llevas ganado y en qué países está tu dinero |

Los datos salen de internet en el momento. No hay cuentas, no hay registro y
**nada de lo que escribas sale de tu ordenador**.

---

## Paso 0 · Abrir la terminal

La terminal es una ventana donde escribes órdenes en vez de hacer clic.

**Windows** → pulsa la tecla `Windows`, escribe `powershell` y pulsa Enter.

**Mac** → pulsa `Cmd + Espacio`, escribe `terminal` y pulsa Enter.

**Linux** → `Ctrl + Alt + T`.

**Deberías ver:** una ventana con texto y un cursor parpadeando. Ya está, eso es
todo. No la cierres hasta el final.

---

## Paso 1 · Comprobar que tienes Python

Python es el lenguaje en el que está escrito el panel. Escribe:

**Windows**
```
py --version
```

**Mac y Linux**
```
python3 --version
```

**Deberías ver:** algo como `Python 3.12.3`. El número puede cambiar, pero
**tiene que empezar por 3.11 o más alto**.

> ¿Por qué 3.11 y no 3.10? Porque `pandas` —la pieza que hace todas las cuentas
> de este panel— dejó de funcionar en 3.10 a partir de su versión 3. Si tienes
> 3.10, el programa instalará una `pandas` vieja y los números no serán los
> mismos. En el servidor donde vive el panel original corre **Python 3.14**.

**Si dice que no encuentra el comando**, no lo tienes instalado:

1. Entra en <https://www.python.org/downloads/>
2. Descarga la versión grande que te ofrece
3. **Windows: al instalar, marca la casilla «Add Python to PATH».** Es la de
   abajo del todo en la primera pantalla, y es la que se olvida todo el mundo.
   Si no la marcas, nada de lo que viene después funcionará.
4. Cierra la terminal, abre una nueva y repite el paso 1

---

## Paso 2 · Comprobar que tienes Git

Git es la herramienta que descarga el programa. Escribe:

```
git --version
```

**Deberías ver:** algo como `git version 2.43.0`.

**Si no lo tienes:** descárgalo de <https://git-scm.com/downloads>, instálalo
con las opciones que vienen por defecto (Siguiente, Siguiente, Siguiente),
cierra la terminal y abre una nueva.

> ¿Prefieres no instalar Git? Puedes bajar el programa en `.zip` desde
> <https://github.com/alejandrotp97-art/market-zones> con el botón verde
> **Code → Download ZIP**, descomprimirlo y saltar al Paso 4. Pero con Git es
> más fácil actualizar después, así que merece la pena.

---

## Paso 3 · Descargar el programa

Primero colócate en tu carpeta personal:

**Windows**
```
cd $HOME
```

**Mac y Linux**
```
cd ~
```

Y ahora descarga:

```
git clone https://github.com/alejandrotp97-art/market-zones.git
```

**Deberías ver:** varias líneas que terminan en algo como
`Resolving deltas: 100% ... done.`

Entra en la carpeta que se acaba de crear:

```
cd market-zones
```

**Deberías ver:** que el texto a la izquierda del cursor ahora incluye
`market-zones`. Eso significa que estás dentro.

---

## Paso 4 · Crear el entorno

Esto crea una cajita aparte para las piezas que necesita el programa, para que
no se mezclen con el resto de tu ordenador. Se hace **una sola vez**.

**Windows**
```
py -m venv .venv
```

**Mac y Linux**
```
python3 -m venv .venv
```

**Deberías ver:** nada. Tarda unos segundos y vuelve el cursor. En la terminal,
que no diga nada casi siempre significa que ha ido bien.

Ahora **entra** en esa cajita:

**Windows**
```
.venv\Scripts\activate
```

**Mac y Linux**
```
source .venv/bin/activate
```

**Deberías ver:** que al principio de la línea aparece **`(.venv)`**. Así:

```
(.venv) C:\Users\nacho\market-zones>
```

> ### 🔑 Esto es lo más importante de toda la guía
>
> Ese **`(.venv)`** tiene que estar ahí siempre que uses el programa. Si cierras
> la terminal, desaparece, y hay que volver a escribir el comando de activar.
>
> **Es el motivo número uno por el que la gente cree que «se le ha roto» al día
> siguiente.** No se ha roto: sólo hay que volver a entrar en la cajita.

---

## Paso 5 · Instalar las piezas

Con el `(.venv)` visible, escribe:

```
pip install -r requirements.txt
```

**Deberías ver:** un montón de líneas pasando durante **2 a 5 minutos** (se está
descargando bastante), y al final una que empieza por
`Successfully installed ...`.

Esto también se hace **una sola vez**.

---

## Paso 6 · Arrancar el panel

```
python dashboard.py
```

**Deberías ver:**

```
market-zones  ->  http://127.0.0.1:8771
Primera carga lenta: descarga las cotizaciones. Para parar: Ctrl+C
```

Y entonces **la terminal se queda quieta**. Eso es lo correcto: significa que el
panel está funcionando y esperando. No la cierres.

---

## Paso 7 · Abrirlo

Abre tu navegador y entra en:

```
http://127.0.0.1:8771
```

**Deberías ver:** el panel.

> **La primera carga tarda**, a veces un minuto o más, porque está descargando
> las cotizaciones de todos los activos. No recargues nerviosamente: dale
> tiempo. A partir de la segunda vez va rápido.

Ya está instalado. 🎉

---

## Primeros pasos con el panel

### Meter tu cartera

Ve a **Cartera** en el menú. Tienes dos formas:

**A mano**, posición por posición. Necesitas cuatro datos:

| Campo | Qué poner | Ejemplo |
|---|---|---|
| Instrumento | El código del producto, o su ISIN | `NVDA`, `IE00B4L5Y983` |
| Cantidad | Cuántas participaciones o acciones | `2,5` |
| Precio | A cuánto compraste **cada una** | `140` |
| Fecha | Cuándo | `2026-03-15` |

> **El precio va en la moneda del producto, no en euros.** Si compraste una
> acción estadounidense, pon los dólares que costó cada una, aunque tu banco te
> cobrase en euros. Al elegir el instrumento, al lado de «Precio» aparece la
> moneda (`en USD`) para que no haya duda: el panel hace la conversión solo, con
> el cambio del día de la operación. Poner euros ahí mete un error del tamaño
> del tipo de cambio, y el número resultante parece perfectamente normal.

**¿Y los dividendos?** En «Tipo» elige **Dividendo / cupón**. Las casillas
cambian de nombre solas: pon el **importe** que te ingresaron y, en
«Retención», lo que te quitaron. Si tu extracto no dice cuánto tocó por título,
deja «Títulos» vacío y pon el total.

Un dividendo **no cambia tu posición**: no compra ni vende nada, ni toca tu
precio medio. Va a su propia casilla, «Dividendos cobrados», y suma a la
«Rentabilidad total». Si no los apuntas, esa cifra se queda corta.

**¿Metiste un dato mal?** No hace falta borrar nada: en la lista de movimientos,
el botón **✎** de cada fila lo abre en el formulario para corregirlo.

**Importando un fichero** desde tu banco o bróker. Exporta tus movimientos a
CSV o Excel y súbelo. Si vuelves a subir el mismo fichero, **no se duplican las
posiciones**: detecta las repetidas y te dice cuántas se ha saltado.

> Si no encuentra el producto por su nombre, busca su **ISIN** — el código de 12
> caracteres que empieza por dos letras, tipo `IE00B4L5Y983`. Está en la ficha
> del producto en tu banco. Es la forma fiable de identificarlo, porque los
> nombres comerciales se parecen muchísimo entre sí.

### Sacar una copia de tu cartera

Todo lo demás en este panel se vuelve a calcular solo: los precios, las zonas,
el mapa de países. **Tu libro de movimientos no.** Es lo único que, si se
pierde, se perdió.

Así que tienes dos formas de tenerlo a salvo, y no sobra ninguna:

**1. Descargártelo tú.** En **Cartera**, arriba a la derecha del recuadro de
importar, hay un enlace **«↓ mis movimientos»**. Te baja un CSV con todo. Ese
mismo archivo se puede volver a subir aquí tal cual — es el mismo formato que
lee el importador. Guárdalo donde guardas las cosas importantes.

**2. Que la máquina la copie sola, cada noche.** Sólo si dejas el panel
instalado en un servidor:

```bash
cp market-zones-cartera-backup.service market-zones-cartera-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now market-zones-cartera-backup.timer
```

Guarda los **14 últimos estados distintos** en la carpeta `backups/`. Si un día
no tocas la cartera, no gasta copia. Para ver las que hay:

```bash
python backup_cartera.py --list
```

Y si algún día necesitas volver atrás, esto te dice exactamente qué teclear
(no toca nada por su cuenta, sólo te lo explica):

```bash
python backup_cartera.py --restore backups/cartera-2026-08-18-051500.db
```

> **No copies el fichero `cartera.db` con el ratón mientras el panel está
> abierto.** Puede llevarse la base a medio escribir: abre sin dar ningún error
> y le faltan movimientos. Usa una de las dos formas de arriba.

### Dos números del realizado, y por qué no son el mismo

En «P&L realizado» hay un interruptor: **Medio** y **FIFO**.

- **Medio** reparte el coste entre todos los títulos que tienes. Es como se lee
  una cartera y es lo que el panel enseña por defecto.
- **FIFO** (primero que entra, primero que sale) es el criterio que aplica la
  ley española a los valores homogéneos. Es el que hace falta para la
  declaración.

**Coinciden siempre que cierras una posición entera**, así que la mayoría de los
días verás el mismo número con los dos. Sólo se separan si has vendido una
**parte**. Cuando eso pasa, el panel te enseña el otro debajo en vez de callarse:
esa diferencia es real y no desaparece por no mirarla.

> Esto no es asesoramiento fiscal. Es la misma cuenta hecha con los dos
> criterios, para que sepas que existen y no te lleves la sorpresa en abril.

### Los dos porcentajes de «Rentabilidad», y por qué no sobra ninguno

Un «+89%» sobre una cartera a la que has ido metiendo dinero no se puede
comparar con nada, porque no dice **cuándo** entró cada euro. Por eso hay dos:

- **TWR** — qué tal lo han hecho los activos que elegiste. No la mueve cuándo
  aportaste, y por eso es la única que puedes poner al lado de un índice: un
  índice tampoco recibe aportaciones.
- **TIR** — qué te has llevado tú. Ésta sí cuenta cuándo entró cada euro.

Y lo interesante es **la diferencia entre las dos**. Si tu TIR va por encima
del TWR, aportaste, de media, en buenos momentos. Si va por debajo, los mismos
activos te habrían rendido más entrando de otra forma. Es el único número de
todo el panel que puntúa una decisión tuya y no una del mercado.

> **Por debajo de un año no verás la TIR ni el anualizado.** Convertir un +8%
> de tres meses en un «+36% anual» es proyectar una racha como si fuera una
> tasa. Cuando haya un año de historia aparecen solos.

La comparación con el índice va **sin tus dividendos** a propósito, y lo dice
en pantalla: la serie del índice tampoco lleva los suyos, y enfrentar una cosa
con la otra te regalaría un punto y medio al año de ventaja falsa.

### Lo primero que verás: «Estado de mi cartera»

Ocho casillas arriba del todo, pensadas para que en diez segundos sepas:

| Casilla | Contesta |
|---|---|
| **Tengo** | cuánto vale hoy |
| **He puesto** | cuánto has aportado, ya restado lo que sacaste por ventas |
| **He ganado** | resultado en euros, y el porcentaje desde el principio |
| **Este año** | cómo va el año, desde el cierre del año pasado |
| **Frente a…** | si vas por encima o por debajo de tu índice |
| **Peor caída** | cuánto llegaste a perder, y cuánto te falta de tu mejor momento |
| **Cobertura** | qué parte de tu patrimonio entra de verdad en estos cálculos |
| **Requiere mirada** | cuántas cosas hay debajo esperándote |

> **La casilla de cobertura es la que hace honestas a las demás.** Si dice 84%,
> la rentabilidad, la caída y la correlación describen ese 84% — no toda tu
> cartera. No es que el resto vaya bien: es que no se sabe.

### «Qué merece tu atención»: hechos, nunca órdenes

Debajo aparecen las cosas que conviene que mires: datos que faltan, posiciones
que se han ido de su peso objetivo, cosas que se quedan fuera del análisis.

Cada aviso te dice **qué pasa**, **a qué parte de tu cartera afecta**, **por qué
importa** y **qué dato falta**, si falta alguno.

Ninguno te dirá que compres ni que vendas, y no es por prudencia: este panel
tiene medido que no sabe hacer eso. Un aviso describe algo comprobable; una
orden requiere saber cuándo necesitas el dinero, qué impuestos pagarías y qué
opinas del activo — tres cosas que el programa no sabe.

### Tus aportaciones

Cuánto has ido metiendo, mes a mes, con los meses vacíos incluidos: si un año
estuviste parado, se ve el hueco.

Dos avisos para leerlo bien:

- La **media al mes** reparte entre TODOS los meses transcurridos, no sólo
  entre aquellos en los que aportaste. Lo segundo contesta «cuánto aporto
  cuando aporto», que no es lo que nadie quiere saber — y sale más halagador.
- Mide el dinero que entra en **títulos**, no en tu cuenta del bróker. El panel
  ve compras, no transferencias: un mes con el dinero parado en efectivo sale
  aquí como un mes sin aportar.

### Tu objetivo

Escribe a dónde quieres llegar —capital, aportación mensual, horizonte— y el
panel te dice **cuánto llevas del camino** y si estás cumpliendo tu propio plan
de aportaciones.

> **No verás nunca una fecha de llegada.** Decir «a este ritmo llegas en 2034»
> exige suponer una rentabilidad futura, y ya sabes lo que opina este panel de
> pronosticar. Progreso y desviación son hechos; una fecha es una predicción
> disfrazada de aritmética.

Un campo vacío significa «no lo he decidido», y desaparece del progreso. No es
lo mismo que ponerlo a cero.

### Los botones de temporalidad, y por qué no son un simple recorte

Arriba de la gráfica tienes **1M · 3M · 6M · YTD · 1A · 5A · Todo** y
«Fechas…» para elegir el tramo que quieras. La rentabilidad de abajo cambia con
ellos: son la misma pregunta mirada de dos formas.

Lo que no se ve pero importa: cuando eliges un tramo, la línea del índice se
**resiembra** ese primer día con el valor que tenía tu cartera, y recibe tus
mismos movimientos dentro del tramo. Si nos limitáramos a recortar, estarías
comparando tres meses de tu cartera contra una posición del índice comprada
hace dos años, y casi toda la diferencia que verías sería historia vieja.

Por eso las tres líneas arrancan pegadas, y lo que se abre entre ellas es
exactamente lo que ha pasado **en ese tramo**.

### La caída máxima, y por qué no sale de tu saldo

Un ingreso sube el saldo, y **aportar no es recuperarse**. Si midiéramos la
caída sobre los euros, una transferencia podría devolver la cifra a su máximo
anterior y dar por superada una caída de la que el mercado no ha vuelto — y si
aportas todos los meses, una bajada larga podría no llegar a verse nunca.

Así que se mide sobre **lo que vale 1 € invertido**. Verás tres cosas:

- **Caída máxima** — la peor, con las fechas y cuánto tardó en recuperarse (o
  si sigue sin recuperar).
- **Ahora mismo** — cuánto estás por debajo de tu mejor momento. Suele importar
  más que la anterior: una caída del 30% en 2020 no dice nada de hoy.
- **Volatilidad y Sharpe** — cuánto se mueve y cuánta rentabilidad sacas por
  unidad de movimiento. El Sharpe va con **tipo sin riesgo 0%**, y lo pone al
  lado: un Sharpe sin decir contra qué se calcula no se compara con nada.

### Quién te está haciendo el dinero

«Peso» te dice qué parte de tu dinero está en cada cosa, y «%» cuánto ha subido
cada una. Ninguna de las dos contesta a **quién ha hecho el dinero en euros**:
una posición del 5% que se dobló ha aportado más que una del 40% que subió un
2%. Esa sección lo ordena de mayor a menor, en euros, y suma no realizado,
realizado y dividendos.

### Rebalanceo: comprando, sin vender

Escribe el **peso objetivo** de cada posición y el panel te dice cuánto te has
desviado. Después pon lo que vas a aportar y te reparte esa aportación para
acercarte al objetivo **sin vender nada**.

Es a propósito. Rebalancear vendiendo lo que sobra es lo que hace todo el
mundo, y en España cada venta con plusvalía es un hecho imponible: pagar
impuestos hoy para cuadrar unos decimales de peso destruye más de lo que
corrige. Con dinero nuevo llegas al mismo sitio sin pasar por Hacienda.

> Tus objetivos **no tienen que sumar 100** exacto: se usan normalizados, así
> que lo que manda es la proporción entre ellos. Y una posición sin objetivo se
> queda fuera del reparto — no se cuenta como cero, porque que nadie haya
> decidido su peso no significa que deba desaparecer.

### «Si vendo…»: lo exacto y lo estimado, separados

Eliges una posición, dices cuántos títulos y el panel parte la respuesta en dos
columnas. **La línea entre ellas no es decorativa.**

**A la izquierda, lo que sale de tu libro y se puede comprobar:** qué lotes
concretos consumiría la venta —los más antiguos primero, que es la regla
española—, cuánto costaron, cuánto ingresarías y qué plusvalía o minusvalía
resulta.

**A la derecha, una estimación:** el impuesto. Y es una estimación porque
depende de una ley que cambia y de cosas que este panel no ve.

> **Esto no es asesoramiento fiscal.** El panel no conoce el resto de tus rentas
> del ahorro fuera de esta cartera, ni tus minusvalías de ejercicios anteriores,
> ni si tributas en País Vasco o Navarra, que tienen su propio régimen.

Hay dos casillas para que le digas lo que le falta:

- **Otras ganancias del año.** El panel calcula solo las que salen de esta
  cartera, pero si has vendido algo fuera, escríbelo. Importa más de lo que
  parece: una plusvalía **no tributa siempre al 19%**. Si ese año ya llevas
  ganancias, la nueva paga en tu tramo, no en el más bajo — y multiplicar por
  el 19% es el error más común que se comete con esto.
- **Minusvalías pendientes.** Reducen la base, y lo que sobre sigue pendiente.

**Si la venta sale en pérdidas**, el panel mira si has comprado ese mismo valor
en los dos meses anteriores: con una recompra así de cerca, la minusvalía **no
se puede computar todavía**. Hacia delante no puede saber si vas a recomprar, y
te lo dice en vez de callárselo.

### De qué divisas dependes (y por qué hay dos listas)

Dos lecturas, y **ninguna sobra**:

- **En qué moneda compras y vendes** — exacta, sale de tus movimientos. Sirve
  para una cosa: saber en qué moneda te van a cobrar.
- **Dónde está el negocio** — abre cada fondo y mira en qué países invierte de
  verdad, igual que hace el mapa.

La diferencia suele sorprender. Un fondo indexado mundial **cotizado en euros**
aparece abajo al 100% en euros, y arriba lleva dos tercios en dólares. Tú no
compras dólares en ningún momento, y sin embargo más de la mitad de tu dinero
depende de lo que haga el dólar.

> La divisa se asigna por el **país del negocio**. Una empresa alemana que
> factura la mitad en dólares aparece entera en euros: afinar eso exigiría la
> cuenta de resultados de cada compañía. Y lo que no está en la tabla de
> monedas se dice aparte — no se reparte entre las demás, porque eso inflaría
> todas y haría parecer completa una foto que no lo está.

### Beta: cuánto te mueve el mercado

En «Diversificación real» aparecen dos números juntos, y van juntos a propósito:

- **Beta** — cuánto se ha movido tu cartera por cada 1% que se movió el índice.
- **Correlación** — cuánto de lo que hace tu cartera explica el índice.

**La beta sola engaña.** Una beta de 1,2 con correlación 0,3 no significa «me
muevo un 20% más que el mercado»: significa que el mercado explica muy poco de
lo que hace tu cartera y que ese 1,2 es casi ruido. Por eso el panel las enseña
siempre las dos y te dice cuál de las dos lecturas toca.

### Qué te cuesta, y el coste que nunca ves

Tres cifras, y la tercera es la que cambia decisiones:

- **Comisiones pagadas** — lo que llevas gastado en operar. Estaba guardado en
  cada movimiento y no se sumaba en ningún sitio.
- **Retenido en dividendos** — no es una comisión, es un impuesto a cuenta, y
  parte se recupera al declarar.
- **Gastos corrientes (TER)** — éste **no aparece en ningún extracto**. No te lo
  cobran: se descuenta del valor liquidativo todos los días.

El TER no lo publica la fuente de precios de este panel, así que lo escribes tú:
está en el **KID/DFI** del producto, la ficha de dos páginas que tu banco tiene
que darte. En la tabla de «Qué te cuesta» hay una casilla por posición. Con eso
el panel te dice lo que se llevan a 10 y a 20 años.

> Si dejas casillas vacías, el total **no** cuenta esas posiciones — y te lo
> dice: «sobre el 60% del capital». Un cero diría que no te cuestan nada, y la
> verdad es que no se sabe.

### Diversificación: contar líneas no es diversificar

Cinco fondos del mismo índice son **una** apuesta repartida en cinco filas. La
sección «Diversificación real» mide cuántas apuestas independientes tienes de
verdad, usando cómo se han movido juntas tus posiciones durante el último año.

Si el número de la derecha es mucho menor que el de la izquierda, tienes menos
diversificación de la que parece. Debajo, la pareja que más se parece: por ahí
es por donde empieza a mirarse.

### Qué mirar el primer día

1. **Cartera** → cuánto llevas invertido, cuánto vale hoy, y el mapa de países.
   Ese mapa abre cada fondo y mira qué hay dentro de verdad. Suele sorprender.
   La columna **Zona** te dice, de cada cosa que tienes, si su precio está
   estirado o deprimido respecto a su propia historia.
2. **Régimen** → en qué estado está el mercado en general.
3. **Comité** → al final de la página, **«Mi cartera real»**: pone nota a lo que
   de verdad tienes, con la misma fórmula que usa para su propuesta. Fíjate en
   «activos efectivos»: es la diversificación de verdad, no cuántas líneas
   tienes. Y en «cobertura»: si dice 60%, es que el 40% de tu dinero no está
   medido — que no es lo mismo que estar bien.
4. **Inicio** → busca un activo tuyo y mira su zona con el gráfico entero.

### Que te avise cuando algo entre en un extremo

Sólo si dejas el panel en un servidor. Comprueba cada noche las zonas de lo que
tienes y avisa **cuando una posición entra o sale de Capitulación o Euforia**:

```bash
cp market-zones-alertas.service market-zones-alertas.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now market-zones-alertas.timer
```

La primera vez no avisa de nada: sólo se apunta dónde está cada cosa. A partir
de ahí, sólo habla cuando algo cambia. Para ver la foto de hoy sin esperar:

```bash
python alertas_cartera.py --list
```

Por defecto escribe en el registro del sistema. Si quieres que llegue a
Telegram, mete tus claves sin que acaben en ningún fichero del proyecto:

```bash
systemctl --user edit market-zones-alertas.service
```

y dentro:

```
[Service]
Environment=MZ_TELEGRAM_TOKEN=el-token-de-tu-bot
Environment=MZ_TELEGRAM_CHAT_ID=tu-chat-id
```

> **Ojo con lo que es este aviso.** No dice que compres ni que vendas. Dice que
> una cosa que ya tienes ha cambiado de estado — lee la advertencia de abajo.

### Una advertencia sobre las zonas

Las zonas dicen si un precio está **estirado o deprimido respecto a su propia
historia**. No son una recomendación de compra ni de venta, y no predicen nada.

Que algo esté «en euforia» no significa que vaya a bajar. Que esté «en
acumulación» no significa que vaya a subir. Los precios pueden quedarse
estirados durante años. Es un termómetro, no una bola de cristal.

---

## El día siguiente: cómo volver a arrancarlo

Aquí es donde se atasca todo el mundo. Son **tres** comandos, siempre los
mismos:

**Windows**
```
cd $HOME\market-zones
.venv\Scripts\activate
python dashboard.py
```

**Mac y Linux**
```
cd ~/market-zones
source .venv/bin/activate
python dashboard.py
```

Y abrir <http://127.0.0.1:8771> en el navegador.

**Para pararlo:** vuelve a la terminal y pulsa **`Ctrl + C`**. Tu cartera se
queda guardada; no se pierde nada al parar.

> **Consejo:** guarda estos tres comandos en una nota del móvil o en un fichero
> de texto en el escritorio. Te va a hacer falta más veces de las que crees.

---

## Actualizar a la última versión

Cuando haya cambios nuevos, con el `(.venv)` activado:

```
git pull
pip install -r requirements.txt
```

Tu cartera **no se toca** al actualizar: vive en un fichero aparte que Git
ignora a propósito.

---

## Cuando algo falla

### `python: command not found` o `no se reconoce como un comando`

Python no está instalado, o en Windows no marcaste «Add Python to PATH» al
instalarlo. Vuelve al Paso 1. En Windows, prueba con `py` en vez de `python`.

### `pip: command not found`

Te has olvidado de activar el entorno. Comprueba que ves **`(.venv)`** al
principio de la línea. Si no está, vuelve al final del Paso 4.

### `ModuleNotFoundError: No module named 'flask'`

Lo mismo: falta el `(.venv)`, o no llegó a completarse el Paso 5. Actívalo y
repite `pip install -r requirements.txt`.

### `Address already in use` / `Only one usage of each socket address`

Ya tienes el panel arrancado en otra ventana de terminal. O lo usas, o lo paras
ahí con `Ctrl+C`. También puedes arrancarlo en otro puerto:

```
MZ_PORT=8772 python dashboard.py
```

(en Windows PowerShell: `$env:MZ_PORT=8772; python dashboard.py`)

### La página tarda muchísimo o sale un error de datos

Está descargando cotizaciones de internet y a veces el proveedor va lento o
falla. Espera y recarga. Si un activo concreto no carga nunca, probablemente su
código esté mal escrito.

### La página «Swing» dice «servicio no disponible»

**Es normal y no es un fallo tuyo.** Esa página la sirve un segundo programa
distinto que no forma parte de esta instalación. Todo lo demás funciona igual.

### Se me ha roto todo y no sé qué he tocado

Borra la carpeta `.venv` y repite los pasos 4 y 5. No pierdes tu cartera: está
en `cartera.db`, que es otro fichero.

---

## Preguntas rápidas

**¿Esto envía mis datos a algún sitio?**
No. Tu cartera se guarda en un fichero llamado `cartera.db` dentro de la
carpeta, en tu ordenador. El programa sólo sale a internet para **descargar**
cotizaciones, nunca para subir nada.

**¿Puede verlo alguien más?**
No. El panel escucha únicamente en `127.0.0.1`, que significa «sólo esta
máquina». Ni siquiera otro ordenador de tu misma casa puede abrirlo.

**¿Tengo que dejarlo encendido?**
No. Arráncalo cuando quieras mirarlo y párralo con `Ctrl+C` al terminar.

**¿Me dice qué comprar?**
No, y desconfía de cualquier cosa que diga que sí. Esto mide lo que **hay**:
qué tienes, cuánto vale y dónde está. Las decisiones son tuyas.
