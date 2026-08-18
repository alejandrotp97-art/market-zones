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
**tiene que empezar por 3.10 o más alto**.

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
