"""La tarjeta del ciclo en `/cartera`: dónde vive y cómo pinta.

Lo que se protege: que la tarjeta esté en la columna principal con los dos
ganchos que busca su guion, que la página cargue su guion y su hoja, y que el
guion no use `innerHTML` en ningún sitio — pinta titulares de terceros, y con
`innerHTML` un titular con etiquetas sería código en la página.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _js():
    with open(os.path.join(ROOT, "static", "ciclo.js"), encoding="utf-8") as f:
        return f.read()


def _html():
    return D.app.test_client().get("/cartera").get_data(as_text=True)


def test_la_tarjeta_vive_en_la_columna_principal_con_sus_ganchos():
    h = _html()
    main = h[h.index('class="col-main"'):h.index('<aside class="rail">')]
    assert 'id="ciclo-card"' in main
    for gancho in set(re.findall(r'\$\("([^"]+)"\)', _js())):
        assert f'id="{gancho}"' in h, gancho


def test_la_pagina_carga_el_guion_y_la_hoja_del_ciclo():
    h = _html()
    assert re.search(r'<script src="[^"]*ciclo\.js[^"]*"', h)
    assert re.search(r'<link rel="stylesheet" href="[^"]*ciclo\.css[^"]*"', h)


def test_el_guion_nunca_interpreta_html():
    js = _js()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "outerHTML" not in js
    assert "textContent" in js
    # Los enlaces de los titulares sólo si son http(s): nada de javascript:
    assert "/^https?:\\/\\//.test(h.link)" in js
