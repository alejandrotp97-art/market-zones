"""La cartera no se sirve bajo un nombre público sin declarar que hay login.

El panel no autentica a nadie, y en loopback no hace falta. `PUBLIC_HOST` es la
única variable que rompe esa premisa, y el proxy con login que da por supuesta
vive fuera de este repositorio: aquí no hay forma de comprobar que exista.

Estos tests fijan el comportamiento que sí se puede garantizar — negarse a
servir el libro a quien nunca declaró que hay algo delante — y, sobre todo, que
esa negativa cubra las rutas que EXISTEN, no las que alguien recordó escribir
en una lista.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


@pytest.fixture
def publico(monkeypatch):
    """Servido bajo un nombre público y sin declarar autenticación."""
    monkeypatch.setattr(D, "PUBLIC_HOST", "panel.ejemplo.com")
    monkeypatch.setattr(D, "CARTERA_BEHIND_AUTH", False)
    return D.app.test_client()


def _cartera_rules():
    """Las rutas de cartera que la aplicación tiene HOY, sacadas del url_map.

    Enumerarlas en vez de listarlas a mano es el punto: el día que alguien
    añada `/api/cartera/loquesea`, este test la cubre sin que nadie se acuerde
    de venir aquí. Una lista escrita a mano envejece en silencio.
    """
    out = []
    for r in D.app.url_map.iter_rules():
        if not D._is_cartera_path(str(r).split("<")[0].rstrip("/") or "/"):
            continue
        # `/api/cartera/<int:mid>` -> `/api/cartera/1`
        url = str(r).replace("<int:mid>", "1")
        if "<" in url:
            continue                       # ninguna hoy; si aparece, se añade
        method = "GET"
        for m in ("GET", "POST", "DELETE", "PUT"):
            if m in r.methods:
                method = m
                break
        out.append((url, method))
    return sorted(out)


def test_hay_rutas_de_cartera_que_cubrir():
    """Si el descubrimiento se rompe, los tests de abajo pasarían en vacío."""
    rules = _cartera_rules()
    assert len(rules) >= 6, rules
    assert ("/cartera", "GET") in rules
    assert ("/api/cartera/export", "GET") in rules


@pytest.mark.parametrize("url,method", _cartera_rules())
def test_ninguna_ruta_de_cartera_responde_bajo_nombre_publico(publico, url, method):
    # La cabecera CSRF va puesta a propósito: sin ella el guard de CSRF
    # devolvería 403 antes y estaríamos comprobando el cerrojo equivocado.
    r = publico.open(url, method=method, headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 403, f"{method} {url} respondió {r.status_code}"
    assert b"CARTERA_BEHIND_AUTH" in r.data, f"{url} no explica cómo arreglarlo"


def test_en_loopback_la_cartera_funciona_como_siempre(monkeypatch):
    """Sin PUBLIC_HOST no hay nada que cerrar: es el despliegue de toda la vida
    y este cambio no puede haberlo tocado."""
    monkeypatch.setattr(D, "PUBLIC_HOST", "")
    monkeypatch.setattr(D, "CARTERA_BEHIND_AUTH", False)
    c = D.app.test_client()
    assert c.get("/cartera").status_code == 200
    assert c.get("/api/cartera").status_code == 200


def test_declarar_autenticacion_delante_reabre_la_cartera(monkeypatch):
    monkeypatch.setattr(D, "PUBLIC_HOST", "panel.ejemplo.com")
    monkeypatch.setattr(D, "CARTERA_BEHIND_AUTH", True)
    c = D.app.test_client()
    assert c.get("/cartera").status_code == 200
    assert c.get("/api/cartera").status_code == 200


def test_solo_se_retira_la_cartera_no_el_resto_del_panel(publico):
    """Zonas, régimen y screener son precios públicos: cerrarlos no protege a
    nadie y rompe el panel para el visitante que sí debe verlo."""
    for url in ("/", "/regime", "/screener", "/comite"):
        assert publico.get(url).status_code == 200, url


def test_la_pagina_contesta_en_html_y_la_api_en_json(publico):
    pagina = publico.get("/cartera")
    assert "text/html" in pagina.headers["Content-Type"]

    api = publico.get("/api/cartera")
    assert "application/json" in api.headers["Content-Type"]
    assert "CARTERA_BEHIND_AUTH" in api.get_json()["error"]


def test_el_limite_es_el_segmento_no_el_prefijo_de_texto():
    """`startswith("/api/cartera")` daría por cubierta `/api/carteras-publicas`,
    que es otra cosa. El límite tiene que ser la barra o el final."""
    assert D._is_cartera_path("/cartera")
    assert D._is_cartera_path("/api/cartera")
    assert D._is_cartera_path("/api/cartera/export")
    assert D._is_cartera_path("/api/cartera/1")
    assert not D._is_cartera_path("/api/carteras-publicas")
    assert not D._is_cartera_path("/carteras")
    assert not D._is_cartera_path("/api/zones")
    assert not D._is_cartera_path("/")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
