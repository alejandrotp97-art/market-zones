"""El registro vive en su propia pantalla, y las dos páginas tienen que
seguir siendo una sola aplicación.

Lo que se fija aquí es el REPARTO —qué pieza vive en qué página y cómo se llega
de una a otra—, no cómo se pintan: el diario y el libro se prueban por su API en
`tests/test_cartera_diario.py` y `tests/test_cartera.py`.

Lo que rompe si esto se afloja: el ✎ del libro lleva a `/cartera?editar=<id>`
porque el formulario de alta NO se movió. Duplicar el libro en las dos páginas, o
llevarse el formulario, deja ese botón apuntando a un hueco vacío.
"""
import os
import sys

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


def _html(cli, url):
    r = cli.get(url)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)


def test_la_pagina_del_registro_existe(cli):
    assert "Diario y movimientos" in _html(cli, "/cartera/movimientos")


def test_el_registro_se_lo_lleva_entero(cli):
    """Diario y libro, los dos, en la página nueva. La mitad de una mudanza es
    peor que ninguna: obliga a mirar en dos sitios para leer una cronología."""
    h = _html(cli, "/cartera/movimientos")

    assert 'id="diario"' in h
    assert 'id="mov-body"' in h


def test_la_cartera_ya_no_carga_el_registro(cli):
    """Si se quedaran también aquí no habría mudanza ninguna, sólo una copia: la
    página seguiría igual de larga y habría dos libros que mantener."""
    h = _html(cli, "/cartera")

    assert 'id="mov-body"' not in h
    assert 'id="diario"' not in h


def test_hay_camino_de_ida_y_de_vuelta(cli):
    """Una página a la que sólo se llega escribiendo la URL no existe para quien
    la usa."""
    assert "/cartera/movimientos" in _html(cli, "/cartera")
    assert 'href="/cartera"' in _html(cli, "/cartera/movimientos")


def test_el_alta_y_la_importacion_NO_se_movieron(cli):
    """No se fueron de esta página —dentro de ella sí cambiaron de sitio, y eso
    lo fija el test de abajo—. Es lo que hace que el ✎ del libro pueda ser un
    enlace en vez de un segundo formulario: si esto se rompe, `?editar=` deja de
    tener destino."""
    h = _html(cli, "/cartera")

    assert 'id="add-form"' in h        # alta manual
    assert 'id="drop"' in h            # importación CSV
    assert 'id="add-card"' in h        # el ancla a la que salta `?editar=`


def test_el_alta_va_arriba_no_al_final(cli):
    """Anotar una compra es lo ÚNICO que se viene a HACER a esta pantalla; todo
    lo demás se lee. Estaba a veinte tarjetas, detrás del mapa, y llegar hasta
    ella costaba media página de scroll.

    Se fija por posición relativa y no por un índice: lo que importa es que la
    entrada quede después del resumen y ANTES de lo que se lee. Añadir mañana
    otra tarjeta de análisis no puede hacer fallar esto."""
    h = _html(cli, "/cartera")

    resumen = h.index('id="estado-card"')
    alta = h.index('id="add-card"')

    assert resumen < alta
    for despues in ('class="card chart-card"',   # evolución
                    'id="perf-card"',            # rentabilidad
                    'id="pos-legend"',           # posiciones
                    'id="corr-card"',            # diversificación
                    'id="drop"'):                # importar CSV
        assert alta < h.index(despues), despues


def test_la_cartera_sabe_atender_editar(cli):
    """El contrato entre las dos páginas es el parámetro `editar` de la URL, y
    vive entero en el cliente: aquí se comprueba que quien lo lee sigue estando
    servido en la página que lo recibe."""
    h = _html(cli, "/cartera")
    js = (D.app.test_client().get("/static/cartera.js")).get_data(as_text=True)

    assert "cartera.js" in h
    assert '"editar"' in js
    assert "add-card" in js


def test_el_libro_manda_a_corregir_a_la_cartera(cli):
    js = (D.app.test_client().get("/static/cartera-movs.js")).get_data(as_text=True)

    assert "/cartera?editar=" in js


def test_las_dos_pantallas_comparten_la_preferencia_del_ojo(cli):
    """Tapar los importes en una y verlos destapados en la otra sería no
    haberlos tapado. La clave de `localStorage` tiene que ser la misma."""
    a = (D.app.test_client().get("/static/cartera.js")).get_data(as_text=True)
    b = (D.app.test_client().get("/static/cartera-movs.js")).get_data(as_text=True)

    assert '"cartera-hide"' in a
    assert '"cartera-hide"' in b
