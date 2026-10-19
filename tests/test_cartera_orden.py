"""Las tarjetas de `/cartera` se reordenan arrastrándolas por su asa.

Lo que se protege: que la página cargue el guion y la hoja del orden, y que
cada tarjeta de las dos columnas tenga una clave única y estable (su `id` o un
`data-card`). El orden se guarda como lista de claves: una tarjeta sin clave no
se podría mover y dos con la misma se pisarían al restaurar el orden.
"""
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


def _html():
    return D.app.test_client().get("/cartera").get_data(as_text=True)


class _Cards(HTMLParser):
    """Hijos directos de `.col-main` y `.rail` con clase `card` o `hero`."""

    VOID = frozenset({"input", "br", "img", "hr", "meta", "link", "source", "wbr"})

    def __init__(self):
        super().__init__()
        self.stack, self.cards = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        parent = self.stack[-1] if self.stack else None
        if parent in ("main", "rail") and ({"card", "hero"} & set(cls)):
            self.cards.append((parent, a.get("id") or a.get("data-card")))
        if tag in self.VOID:
            return
        self.stack.append("main" if "col-main" in cls else "rail" if "rail" in cls else tag)

    def handle_endtag(self, tag):
        if tag not in self.VOID and self.stack:
            self.stack.pop()


def test_la_pagina_carga_el_guion_y_la_hoja_del_orden():
    h = _html()
    assert re.search(r'<script src="[^"]*orden\.js[^"]*"', h)
    assert re.search(r'<link rel="stylesheet" href="[^"]*orden\.css[^"]*"', h)


def test_cada_tarjeta_tiene_una_clave_unica():
    p = _Cards()
    p.feed(_html())
    claves = [k for _, k in p.cards]
    assert {c for c, _ in p.cards} == {"main", "rail"}
    assert len(claves) >= 20 and all(claves), [c for c in p.cards if not c[1]]
    assert len(set(claves)) == len(claves)
    for k in ("evolucion", "posiciones", "importar", "ciclo-card", "comp-card"):
        assert k in claves, k
