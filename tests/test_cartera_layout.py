"""La cartera son dos columnas: el relato a la izquierda y la consulta a la
derecha.

Lo que se fija aquí es el ARMAZÓN —qué vive en qué columna, que el mapa sea uno
solo y que el diálogo tenga con qué ampliarlo—, no cómo se pinta cada tarjeta.

Lo que rompe si esto se afloja: el mapa se MUDA al diálogo al ampliarlo y vuelve
al cerrarlo. Duplicarlo en las dos pantallas serían dos geometrías, dos descargas
y dos estados que se separan en cuanto se toca el filtro de clase — y el día que
se separan, ninguna de las dos dice la verdad.
"""
import os
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


def _js(nombre):
    r = D.app.test_client().get("/static/" + nombre)
    assert r.status_code == 200
    return r.get_data(as_text=True)


class _Arbol(HTMLParser):
    """Lo justo para preguntar por ANCESTROS y por hijos directos.

    Buscar `id="wmap"` en la cadena dice que el mapa existe; no dice en qué
    columna está, que es justo lo que aquí importa.
    """

    def __init__(self):
        super().__init__()
        self.pila, self.nodos = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        nodo = {"tag": tag, "id": a.get("id", ""), "clases": (a.get("class") or "").split(),
                "hidden": "hidden" in a, "padres": list(self.pila), "hijos": []}
        if self.pila:
            self.pila[-1]["hijos"].append(nodo)
        self.nodos.append(nodo)
        if tag not in ("meta", "link", "br", "img", "input", "hr", "path", "circle", "line"):
            self.pila.append(nodo)

    def handle_endtag(self, tag):
        if self.pila and self.pila[-1]["tag"] == tag:
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
    return a


def _dentro_de(nodo, clase):
    return any(clase in p["clases"] for p in nodo["padres"])


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


def test_el_mapa_sabe_mudarse_y_volver(cli):
    """El diálogo necesita un sitio de destino, el raíl un hueco al que
    devolverlo, y el bloque que viaja tiene que ser uno solo."""
    a, js = _arbol(cli), _js("cartera-map.js")

    a.por_id("map-dlg")
    a.por_id("map-dlg-host")
    assert _dentro_de(a.por_id("geo-body"), "rail")   # de casa, en el raíl
    assert a.por_id("geo-body")["padres"][-1]["id"] == "geo-slot"

    for pieza in ("geo-body", "map-dlg-host", "geo-slot", "showModal"):
        assert pieza in js, pieza


def test_el_selector_de_clase_vive_en_el_dialogo(cli):
    """Filtrar es algo que se hace mirando el mapa grande. Arriba sólo hace
    falta SABER qué filtro está puesto, y para eso está la etiqueta."""
    a = _arbol(cli)

    assert _dentro_de(a.por_id("geo-class"), "map-dlg")
    a.por_id("geo-filter")
    assert "geo-filter" in _js("cartera-map.js")     # y alguien la escribe


def test_las_tres_lecturas_de_composicion_comparten_tarjeta(cli):
    """Países, tipo de activo y divisas responden a la MISMA pregunta y nunca se
    comparan entre sí: se turnan en el mismo hueco."""
    a = _arbol(cli)

    for pane in ("pane-geo", "pane-clase", "pane-ccy"):
        assert _dentro_de(a.por_id(pane), "rail"), pane
    # Y los ganchos que pinta cartera.js siguen existiendo: si alguno se cae al
    # mover las tarjetas, la pestaña se queda en blanco sin dar error.
    for gancho in ("clase", "clase-legend", "ccy", "ccy-legend"):
        a.por_id(gancho)
    assert "comp-tabs" in _js("cartera.js")


def test_las_tablas_anchas_se_quedan_en_la_columna_ancha(cli):
    """La del activo y la divisa tiene cuatro columnas y la de correlación es
    una matriz que crece con cada posición. En 360px habría que elegir entre
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

    mapa = a.por_id("comp-card")["padres"][-1]
    for ident in ("contrib-card", "rebal-card", "aport-card", "plan-card"):
        panel = a.por_id(ident)
        assert _dentro_de(panel, "rail"), ident
        # Debajo del mapa, no encima: el mapa es lo primero del raíl.
        assert mapa["hijos"].index(panel) > mapa["hijos"].index(a.por_id("comp-card")), ident


def _regla(css, selector):
    """El cuerpo de una regla CSS, buscada por su selector exacto."""
    marca = selector + "{"
    i = css.find(marca)
    assert i >= 0, f"no existe la regla {selector}"
    return css[i + len(marca):css.index("}", i)]


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
    columnas en vez de inventarse un ancho nuevo."""
    css = _js("cartera.css")

    for selector in (".estado-grid", ".cart-hero", ".perf-grid", ".add-grid"):
        cuerpo = _regla(css, selector)
        assert "repeat(4,1fr)" in cuerpo, (selector, cuerpo)

    for selector in (".estado-grid", ".perf-grid", ".add-grid", ".avisos", ".perf-two"):
        assert "14px" in _regla(css, selector), selector

    # Los cuatro números no van dentro de una tarjeta: sin este sangrado —borde
    # más relleno— arrancan 15px antes que todo lo demás y la columna se tuerce.
    assert "padding:0 15px" in _regla(css, ".cart-hero")
    # Tres cajas sobre cuatro columnas: la última se lleva las dos que sobran.
    assert "span 2" in _regla(css, ".perf-grid>*:last-child")


def test_ningun_grid_de_dos_columnas_va_cojo(cli):
    """`.two` reparte el ancho en dos SIEMPRE, tenga o no dos tarjetas que
    poner. Con un número impar —o con una que puede ocultarse sola— la fila
    queda con medio hueco vacío al lado, que es exactamente lo que pasaba con
    «El activo y la divisa» en las carteras que están todas en euros.

    Hoy no queda ninguno en esta página. Se deja como guardia: el día que
    alguien vuelva a emparejar dos tarjetas, el reparto tiene que estar
    completo."""
    a = _arbol(cli)

    for g in a.con_clase("two"):
        secciones = [h for h in g["hijos"] if h["tag"] == "section"]
        assert len(secciones) == 2, [s["id"] for s in secciones]
        assert not any(s["hidden"] for s in secciones), [s["id"] for s in secciones]
