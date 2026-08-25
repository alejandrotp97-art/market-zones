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


# El juego COMPLETO de elementos vacíos de HTML más los del SVG que usa el mapa.
# Con la lista corta, meter un `<source>` o un `<use>` en la plantilla dejaba al
# analizador desincronizado y toda la ascendencia pasaba a ser mentira.
_VACIOS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
           "meta", "param", "source", "track", "wbr",
           "path", "circle", "line", "rect", "polygon", "polyline", "ellipse",
           "use", "stop"}


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


def _hasta_cerrar(js, ancla, abre, cierra):
    """El trozo que va desde `ancla` hasta que se equilibra el primer `abre`
    que aparece DESPUÉS de ella.

    Es lo que permite preguntar por lo que hay DENTRO de un oyente en vez de
    buscar palabras sueltas por todo el fichero: las palabras sobreviven a que
    borres el oyente, el cuerpo no.
    """
    i = js.find(ancla)
    assert i >= 0, f"no aparece {ancla!r}"
    j = js.index(abre, i + len(ancla))
    hondo = 0
    for k in range(j, len(js)):
        if js[k] == abre:
            hondo += 1
        elif js[k] == cierra:
            hondo -= 1
            if hondo == 0:
                return js[i:k + 1]
    raise AssertionError(f"{abre!r} sin cerrar tras {ancla!r}")


def _cuerpo(js, ancla):
    """El bloque `{…}` que sigue a `ancla`: el cuerpo de una función o de un
    oyente escrito con llaves."""
    return _hasta_cerrar(js, ancla, "{", "}")


def _llamada(js, ancla):
    """La llamada `(…)` que sigue a `ancla`, para las de una sola línea que no
    abren bloque."""
    return _hasta_cerrar(js, ancla, "(", ")")


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

    ampliar = _cuerpo(js, "const ampliar =")
    assert "appendChild" in ampliar and "geo-body" in ampliar
    assert "showModal" in ampliar


def test_el_mapa_VUELVE_a_su_hueco_al_cerrar(cli):
    """El regreso es la mitad frágil de la mudanza, y la que se rompe sin hacer
    ruido: el mapa se queda dentro de un diálogo cerrado y el raíl vacío.

    Se mira DENTRO del oyente de `close` a propósito. Comprobar que las palabras
    «geo-slot» y «appendChild» aparecen en el fichero no vale: siguen apareciendo
    después de borrar el oyente entero, que es justo el fallo a cazar. Y va en
    `close` y no en el botón porque el <dialog> también se cierra con Esc y
    pulsando fuera — los tres caminos tienen que devolver el mapa."""
    js = _estatico("cartera-map.js")

    cierre = _cuerpo(js, 'dlg.addEventListener("close"')
    assert "appendChild" in cierre, cierre
    assert "geo-body" in cierre, cierre
    assert "hueco" in cierre or "geo-slot" in cierre, cierre

    # Y los tres caminos existen: el botón, y el clic fuera. Esc lo pone el
    # propio <dialog> por ser modal.
    assert "close()" in _llamada(js, "cerrar.addEventListener")


def test_arrastrar_la_barra_del_dialogo_no_lo_cierra(cli):
    """El diálogo lleva relleno propio y su contenido pasa de 92vh, así que tiene
    barra de desplazamiento. Tanto el margen interior como el canal de la barra
    se anuncian como el propio <dialog>, de modo que `ev.target === dlg` a secas
    los trata como «fuera»: arrastrar la barra cerraba el mapa y lo devolvía al
    raíl a media lectura. Fuera es fuera del RECTÁNGULO."""
    js = _estatico("cartera-map.js")

    fuera = _cuerpo(js, 'dlg.addEventListener("click"')
    assert "getBoundingClientRect" in fuera, fuera
    assert "clientX" in fuera and "clientY" in fuera, fuera


def test_el_mapa_no_depende_de_su_propio_herraje(cli):
    """Ampliar es un accesorio. Cuando el herraje se desreferenciaba en el nivel
    superior del módulo, un botón de menos en la plantilla lanzaba un TypeError
    antes de `load()` y el mapa dejaba de pedir sus datos, de repintar y de
    enterarse de que la cartera había cambiado: un botón decorativo tumbando la
    descarga entera.

    Ahora va tras una guarda y después de lo esencial."""
    js = _estatico("cartera-map.js")

    montaje = _cuerpo(js, "function montarAmpliar")
    for pieza in ("map-dlg", "map-dlg-host", "geo-slot", "map-expand", "map-close"):
        assert pieza in montaje, pieza
    # La guarda sale ANTES de colgar nada: si falta una pieza, no se monta
    # media cosa.
    guarda = re.search(r"if \(![^)]*\)\s*return;", montaje)
    assert guarda, montaje
    assert guarda.start() < montaje.index("addEventListener"), montaje
    # Y lo esencial va antes: el mapa pide sus datos y queda vivo, y sólo
    # después se le cuelga lo accesorio.
    assert js.index("load();") < js.rindex("montarAmpliar();")


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
    # Y al revés: una excepción que ya no hace falta se retira, o la lista se
    # convierte en un cajón donde esconder ganchos rotos.
    sobran = sorted(i for i in _PINTADOS_EN_CALIENTE if i not in ids)
    assert not sobran, sobran


def test_el_selector_de_clase_vive_en_el_dialogo(cli):
    """Filtrar es algo que se hace mirando el mapa grande. Arriba sólo hace
    falta SABER qué filtro está puesto, y para eso está la etiqueta."""
    a = _arbol(cli)

    assert _dentro_de(a.por_id("geo-class"), "map-dlg")
    a.por_id("geo-filter")
    # Y alguien la escribe: dentro del oyente del selector, no en cualquier
    # parte del fichero.
    assert "geo-filter" in _cuerpo(_estatico("cartera-map.js"),
                                   '$("geo-class").addEventListener')


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

    conmutador = _cuerpo(_estatico("cartera.js"), '$("comp-tabs").addEventListener')
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
        assert "gap:14px" in _regla(css, selector), selector

    # Los cuatro números no van dentro de una tarjeta: sin este sangrado —borde
    # más relleno— arrancan 15px antes que todo lo demás y la columna se tuerce.
    assert "padding:0 15px" in _regla(css, ".cart-hero")
    # Tres cajas sobre cuatro columnas: la última se lleva las dos que sobran.
    assert "span 2" in _regla(css, ".perf-grid>*:last-child")
