"""La cartera son dos columnas: el relato a la izquierda y la consulta a la
derecha.

Lo que se fija aquí es el ARMAZÓN —qué vive en qué columna, que el mapa sea uno
solo, que el diálogo tenga con qué ampliarlo y que las cajas de dentro caigan
todas sobre la misma rejilla—, no cómo se pinta cada tarjeta.

Lo que rompe si esto se afloja: el mapa se MUDA al diálogo al ampliarlo y vuelve
al cerrarlo. Duplicarlo en las dos pantallas serían dos geometrías, dos descargas
y dos estados que se separan en cuanto se toca el filtro de clase — y el día que
se separan, ninguna de las dos dice la verdad.

Aquí no se comprueba que exista una cadena en un fichero. Se comprueba el TROZO
de código que hace el trabajo: una aserción que sigue en verde después de borrar
lo que dice vigilar no vigila nada.
"""
import os
import re
import sys
from html.parser import HTMLParser

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "CARTERA_DB", str(tmp_path / "cartera.db"))
    monkeypatch.setattr(D, "_instrument_ccy", lambda s: "EUR")
    monkeypatch.setattr(D, "_fx_series_eur", lambda c: None)
    monkeypatch.setattr(D, "_fx_now", lambda c: 1.0)
    monkeypatch.setattr(D, "_last_price", lambda t: 120.0)
    monkeypatch.setattr(D, "_seed_geo_async", lambda *a, **k: None)
    monkeypatch.setattr(D, "PUBLIC_HOST", "")
    monkeypatch.setattr(D, "_close_series",
                        lambda t: pd.Series([100.0 + i * 0.1 for i in range(300)],
                                            index=pd.bdate_range("2024-01-01", periods=300)))
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    return D.app.test_client()


def _html(cli):
    r = cli.get("/cartera")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _estatico(nombre):
    """Un fichero servido de `static/`: hoja de estilos o guion, indistintamente."""
    r = D.app.test_client().get("/static/" + nombre)
    assert r.status_code == 200
    return r.get_data(as_text=True)


# El juego COMPLETO de elementos vacíos de HTML, y SÓLO ésos. Con la lista corta,
# meter un `<source>` en la plantilla desincronizaba el analizador y toda la
# ascendencia pasaba a ser mentira.
#
# Los del SVG NO van aquí aunque en la plantilla se escriban solos: `path`,
# `circle`, `use` y compañía ADMITEN hijos —un `<title>` dentro es el patrón
# normal de nombre accesible—, así que tratarlos como vacíos colgaría a ese hijo
# del abuelo y respondería mal a cualquier pregunta sobre él. Hoy la plantilla
# no trae ninguno con hijos (los `<title>` del mapa los crea el guion en el
# navegador, y por aquí no pasan), pero la regla se escribe por lo que el
# elemento PUEDE llevar, no por lo que hoy lleva.
#
# Se apoya en que `HTMLParser` reparte `<path/>` en un `handle_starttag` más un
# `handle_endtag`, que es lo que hace por defecto: por eso abren y cierran
# solos sin necesidad de listarlos.
_VACIOS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
           "meta", "param", "source", "track", "wbr"}


class _Arbol(HTMLParser):
    """Lo justo para preguntar por ANCESTROS y por orden entre hermanos.

    Buscar `id="wmap"` en la cadena dice que el mapa existe; no dice en qué
    columna está, que es justo lo que aquí importa.

    Cada nodo lleva su número de orden en el documento. Comparar posiciones con
    `list.index` sobre los propios nodos exigiría igualdad por valor de unos
    dicts que se apuntan entre sí, y ahí un fallo sale como recursión o como
    ValueError en vez de como una aserción legible.
    """

    def __init__(self):
        super().__init__()
        self.pila, self.nodos = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        nodo = {"tag": tag, "id": a.get("id", ""), "clases": (a.get("class") or "").split(),
                "hidden": "hidden" in a, "padres": list(self.pila), "hijos": [],
                "orden": len(self.nodos)}
        if self.pila:
            self.pila[-1]["hijos"].append(nodo)
        self.nodos.append(nodo)
        if tag not in _VACIOS:
            self.pila.append(nodo)

    def handle_endtag(self, tag):
        if tag in _VACIOS:
            return
        # Se cae con estruendo en vez de ignorar el desajuste: una etiqueta mal
        # cerrada deja ancestros rancios en la pila, y a partir de ahí cualquier
        # pregunta por la columna en la que vive un nodo se contesta sola y mal.
        assert self.pila, f"</{tag}> sin abrir en la plantilla"
        assert self.pila[-1]["tag"] == tag, (
            f"</{tag}> cierra un <{self.pila[-1]['tag']}> en la plantilla")
        self.pila.pop()

    def por_id(self, ident):
        for n in self.nodos:
            if n["id"] == ident:
                return n
        raise AssertionError(f"no existe #{ident} en la página")

    def con_clase(self, clase):
        return [n for n in self.nodos if clase in n["clases"]]


def _arbol(cli):
    a = _Arbol()
    a.feed(_html(cli))
    assert not a.pila, [n["tag"] for n in a.pila]      # nada quedó sin cerrar
    return a


def _dentro_de(nodo, clase):
    return any(clase in p["clases"] for p in nodo["padres"])


def _bajo(nodo, ident):
    return any(p["id"] == ident for p in nodo["padres"])


def _lineas(js):
    return js.split("\n")


def _es_comentario(linea):
    return linea.lstrip().startswith(("//", "*", "/*"))


def _linea(js, ancla):
    """La línea de código —no de comentario— que contiene `ancla`."""
    for l in _lineas(js):
        if ancla in l and not _es_comentario(l):
            return l
    raise AssertionError(f"no aparece {ancla!r} fuera de un comentario")


def _bloque(js, ancla):
    """Las líneas del bloque que abre `ancla`, hasta el cierre a su misma sangría.

    Por SANGRÍA y no contando llaves. Contar llaves obliga a saber de cadenas,
    de plantillas anidadas, de expresiones regulares y de comentarios, y
    equivocarse en cualquiera de las cuatro devuelve un trozo cortado o de más
    sin avisar — y entonces cada `assert X in trozo` degenera en una búsqueda
    por todo el fichero que se aprueba sola. Es un contrato más pobre, pero uno
    que no miente: la sangría de este código es regular y cualquier cambio la
    pasa por aquí.

    El ancla se busca en código y no en comentarios: un comentario que
    mencione la línea por delante de la línea real secuestraría el trozo.
    """
    lineas = _lineas(js)
    i = next((k for k, l in enumerate(lineas)
              if ancla in l and not _es_comentario(l)), None)
    if i is None:
        raise AssertionError(f"no aparece {ancla!r} fuera de un comentario")
    sangria = len(lineas[i]) - len(lineas[i].lstrip())
    for j in range(i + 1, len(lineas)):
        m = lineas[j]
        if m.strip().startswith("}") and (len(m) - len(m.lstrip())) == sangria:
            return "\n".join(lineas[i:j + 1])
    raise AssertionError(f"el bloque de {ancla!r} no se cierra a su sangría")


def _solo_codigo(bloque):
    """El bloque sin sus líneas de comentario.

    Imprescindible para cualquier comparación de ORDEN: los comentarios de aquí
    explican lo que hace el código y por tanto nombran las mismas llamadas, así
    que un `bloque.index("showModal")` acababa señalando el comentario que está
    por encima de las dos líneas, y la comparación no podía fallar nunca.
    """
    return "\n".join(l for l in _lineas(bloque) if not _es_comentario(l))


def _orden_en_el_arranque(js, *sentencias):
    """Los números de línea de unas sentencias del ARRANQUE del módulo: las que
    van a dos espacios de sangría, ya fuera de toda función.

    Buscarlas por texto suelto cogía la llamada de dentro de un oyente, que está
    más arriba pase lo que pase y hacía imposible que la comprobación fallara.
    """
    sitios = []
    for s in sentencias:
        for i, l in enumerate(_lineas(js)):
            if l == "  " + s:
                sitios.append(i)
                break
        else:
            raise AssertionError(f"{s!r} no está en el arranque del módulo")
    return sitios


def test_el_mapa_esta_en_el_rail_y_no_en_la_columna_principal(cli):
    """Es el cambio entero: el mapa dejó de cerrar la página para vivir arriba a
    la derecha, donde se consulta sin bajar."""
    a = _arbol(cli)

    assert _dentro_de(a.por_id("wmap"), "rail")
    assert not _dentro_de(a.por_id("wmap"), "col-main")


def test_hay_UN_mapa_y_no_dos(cli):
    """El grande y el pequeño son el MISMO nodo, que se muda al diálogo y
    vuelve. Dos mapas serían dos geometrías, dos descargas y dos estados que se
    separan al tocar el filtro."""
    h = _html(cli)

    assert h.count('id="wmap"') == 1
    assert h.count('id="geo-rank-body"') == 1


def test_el_mapa_sale_del_rail_al_ampliar(cli):
    """De casa vive en el hueco del raíl, y el diálogo tiene dónde recibirlo."""
    a, js = _arbol(cli), _estatico("cartera-map.js")

    a.por_id("map-dlg")
    a.por_id("map-dlg-host")
    assert _dentro_de(a.por_id("geo-body"), "rail")
    assert a.por_id("geo-body")["padres"][-1]["id"] == "geo-slot"

    montaje = _bloque(js, "function montarAmpliar")
    assert 'bloque = $("geo-body")' in montaje, montaje
    assert 'destino = $("map-dlg-host")' in montaje, montaje

    ampliar = _bloque(js, "const ampliar =")
    assert "destino.appendChild(bloque)" in ampliar, ampliar
    # ABRIR primero y mudar después. Al revés, si `showModal` fallara el bloque
    # ya estaría dentro de un diálogo que nunca se abre —y que por tanto nunca
    # emitirá `close`—, así que el mapa desaparecería del raíl para siempre y sin
    # un solo error en pantalla.
    codigo = _solo_codigo(ampliar)
    assert codigo.index("showModal") < codigo.index("destino.appendChild"), ampliar
    # Y la bandera del clic-fuera se desarma al abrir: una pulsación sobre el
    # fondo que nunca llegó a dar un clic —el ratón soltado fuera de la ventana,
    # un menú contextual— la dejaba armada hasta la siguiente apertura.
    assert "empezoFuera = false" in ampliar, ampliar


def test_el_mapa_VUELVE_a_su_hueco_al_cerrar(cli):
    """El regreso es la mitad frágil de la mudanza, y la que se rompe sin hacer
    ruido: el mapa se queda dentro de un diálogo cerrado y el raíl vacío.

    Se mira DENTRO del oyente de `close` a propósito. Comprobar que las palabras
    «geo-slot» y «appendChild» aparecen en el fichero no vale: siguen apareciendo
    después de borrar el oyente entero, que es justo el fallo a cazar. Y va en
    `close` y no en el botón porque el <dialog> también se cierra con Esc y
    pulsando fuera — los tres caminos tienen que devolver el mapa."""
    js = _estatico("cartera-map.js")

    cierre = _bloque(js, 'dlg.addEventListener("close"')
    assert "hueco.appendChild(bloque)" in cierre, cierre

    # Y va PRIMERO. Limpiar el resaltado es cosmético; devolver el mapa es lo
    # único que no puede quedarse sin hacer. Con la limpieza delante, cualquier
    # excepción suya dejaba el mapa varado en un diálogo cerrado y el raíl
    # vacío, sin error visible en pantalla.
    codigo = _solo_codigo(cierre)
    assert codigo.index("hueco.appendChild") < codigo.index("hideTip()"), cierre
    assert "empezoFuera = false" in cierre, cierre

    # Y los tres caminos existen: el botón, y el clic fuera. Esc lo pone el
    # propio <dialog> por ser modal.
    assert "close()" in _linea(js, "cerrar.addEventListener")


def test_arrastrar_la_barra_del_dialogo_no_lo_cierra(cli):
    """El diálogo lleva relleno propio y su contenido pasa de 92vh, así que tiene
    barra de desplazamiento. Tanto el margen interior como el canal de la barra
    se anuncian como el propio <dialog>, de modo que `ev.target === dlg` a secas
    los trata como «fuera»: arrastrar la barra cerraba el mapa y lo devolvía al
    raíl a media lectura. Fuera es fuera del RECTÁNGULO."""
    js = _estatico("cartera-map.js")

    medida = _bloque(js, "const fueraDelPanel =")
    assert "getBoundingClientRect" in medida, medida
    assert "clientX" in medida and "clientY" in medida, medida

    # Y manda dónde EMPEZÓ la pulsación. Seleccionar texto dentro del diálogo y
    # soltar fuera despacha su clic sobre el propio diálogo con coordenadas de
    # fuera; juzgando sólo el clic, arrastrar para seleccionar cerraba el mapa.
    # Un clic sintético llega además sin coordenadas —en el (0,0)—, que también
    # cae fuera de un diálogo centrado.
    assert "empezoFuera" in _linea(js, 'dlg.addEventListener("pointerdown"'), js
    clic = _bloque(js, 'dlg.addEventListener("click"')
    assert "empezoFuera &&" in clic, clic
    # Y manda SÓLO dónde empezó. Exigir además que suelte fuera dejaba sin cerrar
    # el gesto que nace en el fondo y se suelta sobre el panel, que es un clic de
    # fuera de manual.
    assert "fueraDelPanel" not in clic, clic


def test_el_mapa_no_depende_de_su_propio_herraje(cli):
    """Ampliar es un accesorio. Cuando el herraje se desreferenciaba en el nivel
    superior del módulo, un botón de menos en la plantilla lanzaba un TypeError
    antes de `load()` y el mapa dejaba de pedir sus datos, de repintar y de
    enterarse de que la cartera había cambiado: un botón decorativo tumbando la
    descarga entera.

    Ahora va tras una guarda y después de lo esencial."""
    js = _estatico("cartera-map.js")

    for funcion in ("function montarAmpliar", "function montarControles"):
        montaje = _bloque(js, funcion)
        # Cada elemento que se busca queda atado a un nombre, y ese nombre se
        # comprueba ANTES de usarse. Las dos mitades importan: sin la primera, un
        # `$("...")` metido en medio de una expresión se escapa del control; sin
        # la segunda, valía como guarda cualquier `if` que mencionara la variable
        # —`if (dlg.open)`, por ejemplo— aunque fuera después de haberla usado.
        # Sobre el bloque SIN comentarios, como el resto del test: los de aquí
        # explican el código citándolo, así que un comentario que mencione un
        # `$("...")` descuadra el conteo y acusa de un defecto que no existe.
        codigo = _solo_codigo(montaje)
        assert codigo.count('$("') == len(re.findall(r'\w+ = \$\("', codigo)), \
            f"{funcion}: hay búsquedas sueltas, sin nombre al que atarlas"
        buscados = re.findall(r'(\w+) = \$\("([^"]+)"\)', codigo)
        assert buscados, funcion
        for variable, ident in buscados:
            # Comprobar que EXISTE: negada, o a secas. `if (dlg.open)` menciona la
            # variable pero no dice nada de si está, y colaba como guarda.
            comprobada = re.search(
                r"if \([^)]*(?:!" + variable + r"\b|\b" + variable + r"\b(?!\s*\.))", codigo)
            assert comprobada, f"{funcion}: {ident} se usa sin comprobar que exista"
            usada = re.search(r"\b" + variable + r"\.", codigo)
            assert usada is None or comprobada.start() < usada.start(), \
                f"{funcion}: {ident} se usa antes de comprobarlo"

    # Y lo esencial va antes: el mapa pide sus datos y queda vivo, y sólo
    # después se le cuelgan los mandos y el herraje. Se miran las sentencias del
    # ARRANQUE —las de dos espacios de sangría—; buscándolas por texto suelto se
    # cogía el `load();` de dentro del oyente del filtro, que está más arriba
    # pase lo que pase, y la comprobación no podía fallar nunca.
    arranca, mandos, herraje = _orden_en_el_arranque(
        js, "load();", "montarControles();", "montarAmpliar();")
    assert arranca < mandos and arranca < herraje


def test_todos_los_ganchos_del_mapa_existen_en_la_plantilla(cli):
    """Cada `$("...")` de cartera-map.js es una promesa contra la plantilla, y
    romperla no da error de sintaxis: da un `null` que revienta al usarse.

    El mapa no pinta ningún id por su cuenta, así que aquí la regla es entera:
    todos. Es lo que cubre a `map-close` y `map-expand`, que no los sujetaba
    nada."""
    h = _html(cli)
    ids = sorted(set(re.findall(r'\$\("([^"]+)"\)', _estatico("cartera-map.js"))))

    assert ids, "sin ganchos que comprobar, el test no mira nada"
    faltan = [i for i in ids if f'id="{i}"' not in h]
    assert not faltan, faltan


# Los pinta el propio cartera.js dentro de un innerHTML, así que no están —ni
# tienen por qué— en la plantilla. La lista es explícita: un gancho nuevo que no
# esté aquí ni en el HTML es un fallo, no una excepción silenciosa.
_PINTADOS_EN_CALIENTE = {
    "fi-g", "fi-go", "fi-l", "fi-out", "fi-qty", "fi-tk",       # simulador de venta
    "g-capital", "g-horizon", "g-losses", "g-monthly", "plan-form",   # objetivo
    "rb-cash", "rb-go",                                        # rebalanceo
}


def test_todos_los_ganchos_de_la_cartera_existen(cli):
    """Lo mismo para cartera.js, que sí pinta parte de sus propios controles."""
    h = _html(cli)
    ids = set(re.findall(r'\$\("([^"]+)"\)', _estatico("cartera.js")))

    faltan = sorted(i for i in ids if f'id="{i}"' not in h and i not in _PINTADOS_EN_CALIENTE)
    assert not faltan, faltan
    # Y al revés, con dientes. Que un id esté en la lista no puede ser una
    # amnistía: hay que VER quién lo pinta, y en su sitio de pintarlo. Contar
    # menciones no valía —lo satisfacía un comentario, una constante suelta o
    # código muerto—, así que se exige la forma exacta con la que este guion
    # escribe cada uno: el `id="rb-go"` de dentro de la plantilla de texto, o el
    # `campo("g-capital", …)` que la genera.
    js = _estatico("cartera.js")
    for ident in sorted(_PINTADOS_EN_CALIENTE):
        assert ident in ids, f"{ident} ya no lo busca nadie: sobra de la lista"
        assert f'id="{ident}"' in js or f'campo("{ident}"' in js, \
            f"{ident} se busca pero no lo pinta nadie"


def test_el_tooltip_tampoco_da_por_hecho_que_esten_sus_piezas(cli):
    """Endurecer `hideTip` y dejar a `showTipFor` como estaba no compraba nada:
    la segunda se alcanza desde el movimiento del ratón sobre el mapa, desde el
    foco y desde la lista, así que en el mismo escenario que la primera dice
    cubrir —falta un elemento— reventaba igual a la primera pasada del ratón."""
    js = _estatico("cartera-map.js")

    mostrar = _bloque(js, "function showTipFor")
    assert "!tip" in mostrar, mostrar
    assert "if (lista)" in mostrar, mostrar


def test_el_filtro_no_depende_del_rotulo(cli):
    """Filtrar y rotular son dos capacidades, y una guarda común las ata: sin el
    rótulo, los botones del diálogo se quedaban puestos y MUDOS —sin error, sin
    aviso, sin recargar el mapa—. Cada rama con la suya."""
    montaje = _bloque(_estatico("cartera-map.js"), "function montarControles")

    # Sin las llaves: quitarlas alrededor de una sola sentencia es idéntico en
    # comportamiento, y este test hablaba de formato en vez de la guarda.
    for variable in ("selector", "lista", "etiqueta"):
        assert f"if ({variable})" in _solo_codigo(montaje), (
            f"{variable} se usa sin comprobar que exista")


def test_el_cambio_de_tema_no_puede_tumbar_el_modulo(cli):
    """Se suscribe ANTES de `load()`, así que es de los pocos sitios donde una
    API que no exista se lleva por delante la descarga entera. `addEventListener`
    sobre un MediaQueryList es la forma moderna; donde sólo esté la antigua, hay
    que usar la antigua en vez de reventar."""
    js = _estatico("cartera-map.js")

    assert "tema.addEventListener" in js
    assert "tema.addListener" in js
    # `_linea` ya revienta si la cadena sólo aparece en un comentario, así que
    # afirmar que está en lo que devuelve no puede fallar nunca. Lo que hay que
    # exigir es la FORMA: que la moderna se use bajo una guarda, no a pelo.
    assert _linea(js, "tema.addEventListener").lstrip().startswith("if ("), (
        "la API moderna se usa sin comprobar antes que exista")


def test_el_selector_de_clase_vive_en_el_dialogo(cli):
    """Filtrar es algo que se hace mirando el mapa grande. Arriba sólo hace
    falta SABER qué filtro está puesto, y para eso está la etiqueta."""
    a = _arbol(cli)

    assert _dentro_de(a.por_id("geo-class"), "map-dlg")
    a.por_id("geo-filter")
    # Y alguien la escribe: dentro del oyente del selector, no en cualquier
    # parte del fichero.
    # Dentro del oyente del SELECTOR, no en cualquier parte de la función: el
    # cuerpo entero incluye también la rama del ranking, y ahí el rótulo no se
    # actualizaría al cambiar de filtro.
    assert "etiqueta.textContent" in _bloque(_estatico("cartera-map.js"),
                                             'selector.addEventListener("click"')


def test_las_tres_lecturas_de_composicion_comparten_tarjeta(cli):
    """Países, tipo de activo y divisas responden a la MISMA pregunta y nunca se
    comparan entre sí: se turnan en el mismo hueco.

    Y comparten TARJETA, no sólo columna: el conmutador oculta por
    `#comp-card .pane`, así que un panel sacado a otra tarjeta del raíl deja de
    ocultarse y se pintan dos a la vez."""
    a = _arbol(cli)

    for pane in ("pane-geo", "pane-clase", "pane-ccy"):
        assert _bajo(a.por_id(pane), "comp-card"), pane
    # Y los ganchos que pinta cartera.js siguen existiendo: si alguno se cae al
    # mover las tarjetas, la pestaña se queda en blanco sin dar error.
    for gancho in ("clase", "clase-legend", "ccy", "ccy-legend"):
        a.por_id(gancho)

    conmutador = _bloque(_estatico("cartera.js"), '$("comp-tabs").addEventListener')
    assert "#comp-card .pane" in conmutador, conmutador
    assert "hidden" in conmutador, conmutador


def test_las_tablas_anchas_se_quedan_en_la_columna_ancha(cli):
    """La del activo y la divisa tiene cuatro columnas y la de correlación es
    una matriz que crece con cada posición. En el raíl habría que elegir entre
    encogerlas o hacerlas rodar de lado."""
    a = _arbol(cli)

    for ident in ("fx-card", "corr-card", "pos-legend", "add-card"):
        assert _dentro_de(a.por_id(ident), "col-main"), ident


def test_los_paneles_pequenos_van_bajo_el_mapa(cli):
    """Cuatro barras, tres cifras, dos tablas estrechas y un formulario de
    cuatro campos ocupaban DOS bandas enteras de la columna ancha. Ninguno
    necesita ese ancho, y entre ellos y lo que viene después metían casi mil
    píxeles de scroll."""
    a = _arbol(cli)

    rail = a.con_clase("rail")
    assert len(rail) == 1, "el raíl tiene que ser uno y sólo uno"
    mapa = a.por_id("comp-card")
    assert mapa["padres"][-1] is rail[0], "el mapa cuelga directamente del raíl"

    for ident in ("contrib-card", "rebal-card", "aport-card", "plan-card"):
        panel = a.por_id(ident)
        assert panel["padres"][-1] is rail[0], ident
        # Debajo del mapa, no encima: el mapa es lo primero del raíl.
        assert panel["orden"] > mapa["orden"], ident


def _regla(css, selector):
    """El cuerpo de la PRIMERA regla con ese selector, tolerando espacios.

    Anclarse a `selector + "{"` hacía que un reformateo inocuo —una llave
    separada por un espacio— tumbara el test sin que cambiara nada de lo que
    dice vigilar.
    """
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no existe la regla {selector}"
    return m.group(1)


def test_las_cajas_de_dentro_comparten_una_sola_rejilla():
    """Los cortes verticales tienen que caer a plomo de arriba abajo.

    Cada tarjeta partía su interior por su cuenta: «Estado» a cuatro columnas
    con hueco de 1px, los cuatro números a cuatro con hueco de 14 y sin sangrar
    —viven fuera de una tarjeta, así que iban 15px más anchos por lado—, y
    rentabilidad, costes y el alta a TRES. Resultado: cortes en el 33% y el 66%
    conviviendo con cortes en el 25%, el 50% y el 75%, y ninguno alineado con
    el de la caja de arriba.

    La rejilla es una sola: cuatro columnas, hueco de 14, medida sobre el
    interior de la tarjeta. Lo que no viene de cuatro en cuatro ocupa dos
    columnas en vez de inventarse un ancho nuevo.

    Se fija la rejilla de ESCRITORIO. Por debajo de 1180px las anulaciones
    responsivas la desmontan a propósito, y eso es otra cosa que este test no
    tiene que impedir."""
    css = _estatico("cartera.css")

    for selector in (".estado-grid", ".cart-hero", ".perf-grid", ".add-grid"):
        cuerpo = _regla(css, selector)
        assert "repeat(4,1fr)" in cuerpo, (selector, cuerpo)

    # Atado a `gap:` y no a la cifra suelta: con la subcadena a pelo, cambiar el
    # hueco y dejar un margen de 14px en cualquier otra declaración seguía
    # pasando, que es exactamente la regresión que esto vigila.
    for selector in (".estado-grid", ".perf-grid", ".add-grid", ".avisos", ".perf-two"):
        assert re.search(r"(?<![-\w])gap:\s*14px", _regla(css, selector)), selector

    # Los cuatro números no van dentro de una tarjeta: sin este sangrado —borde
    # más relleno— arrancan 15px antes que todo lo demás y la columna se tuerce.
    assert "padding:0 15px" in _regla(css, ".cart-hero")
    # Tres cajas sobre cuatro columnas: la última se lleva las dos que sobran.
    assert "span 2" in _regla(css, ".perf-grid>*:last-child")
