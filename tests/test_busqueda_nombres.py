"""El nombre del activo, de la fuente al formulario.

Medido con el producto que abrió la grieta: el plan de pensiones
`BBVA Plan Megatendencia Tecnología PP`, que en Yahoo sólo existe bajo su
identificador de Morningstar `0P000011YA.F`. Cuatro piezas del mismo camino:

  * el bloque «Yahoo Finance» del desplegable tiene que enseñar el NOMBRE del
    activo — para estos fondos `shortname` ES el símbolo y el nombre vive en
    `longname`, así que con el orden anterior la lista enseña el código dos veces;
  * elegir un resultado de Yahoo tiene que guardar nombre y tipo junto al
    símbolo, que es lo que el formulario manda al servidor. Sin eso el
    movimiento se apunta sin nombre y la cartera sólo puede enseñar el código;
  * pegar el identificador `0P…` tal cual —el camino que documenta
    `docs/BUSCADOR.md`— tiene que resolver nombre y tipo en la entrada, en vez
    de devolverlo en mayúsculas con el nombre vacío;
  * y buscarlo por NOMBRE tiene que encontrarlo: el filtro `region=ES` deja la
    consulta sin un solo resultado, y desde España ese era el único modo
    natural de apuntarlo.

`_resolve_symbol` sigue sin salir a la red para un ticker corriente: eso lo
fija `tests/test_simbolo_libre.py` y aquí no se toca.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


class _FakeResp:
    """Sustituto mínimo de la respuesta de `urlopen`: a `json.load()` le basta."""
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _con_llamadas(monkeypatch, payloads):
    """Sustituye `urlopen`: cada petición consume su propio payload y se apunta
    su URL. El último payload se repite si hubiera más peticiones que payloads."""
    import urllib.request as U
    llamadas = {"urls": []}

    def _open(req, timeout=0):
        llamadas["urls"].append(req.full_url)
        i = min(len(llamadas["urls"]) - 1, len(payloads) - 1)
        return _FakeResp(payloads[i])

    monkeypatch.setattr(U, "urlopen", _open)
    D._search_cache.clear()
    return llamadas


def _con_yahoo(monkeypatch, quotes):
    _con_llamadas(monkeypatch, [{"quotes": quotes}])


def _fondo_bbva():
    """Exactamente lo que devuelve Yahoo para este fondo, medido el 2026-10-03:
    el `shortname` es el propio símbolo y el nombre sólo está en `longname`."""
    return {"symbol": "0P000011YA.F", "quoteType": "MUTUALFUND",
            "exchDisp": "Frankfurt", "shortname": "0P000011YA.F",
            "longname": "BBVA Plan Megatendencia Tecnología PP"}


# ── el desplegable ────────────────────────────────────────────────────────
def test_el_bloque_de_yahoo_ensena_el_nombre_no_el_simbolo_dos_veces(monkeypatch):
    _con_yahoo(monkeypatch, [_fondo_bbva()])
    res = D._yahoo_search("0P000011YA.F")

    assert res[0]["name"] == "BBVA Plan Megatendencia Tecnología PP"
    assert res[0]["symbol"] == "0P000011YA.F"


def test_un_resultado_normal_sigue_con_su_shortname(monkeypatch):
    """La mayoría de los resultados traen el nombre en `shortname` y ese es el
    más corto, el que se lee mejor en una lista. Sólo se salta cuando
    `shortname` no es más que el símbolo repetido."""
    _con_yahoo(monkeypatch, [{
        "symbol": "VWCE.DE", "quoteType": "ETF", "exchDisp": "XETRA",
        "shortname": "Vanguard FTSE All-World",
        "longname": "Vanguard FTSE All-World UCITS ETF USD Accumulating"}])

    assert D._yahoo_search("VWCE")[0]["name"] == "Vanguard FTSE All-World"


def test_sin_nombres_publicados_el_simbolo_sigue_de_respaldo(monkeypatch):
    """Degradar a cadena vacía dejaría la fila sin rotular en una lista donde el
    símbolo es lo único que hay; el símbolo es la última respuesta posible."""
    _con_yahoo(monkeypatch, [{"symbol": "XYZ", "quoteType": "EQUITY"}])

    assert D._yahoo_search("xyz")[0]["name"] == "XYZ"


def test_el_filtro_regional_que_no_lo_conoce_no_deja_la_búsqueda_vacia(monkeypatch):
    """Medido el 2026-10-03: «BBVA Plan Megatendencia» con `region=ES` devuelve
    CERO resultados, y el mismo texto sin ese filtro devuelve el fondo. Estos
    planes no cotizan en ninguna plaza española y el filtro regional se los
    carga, con lo que desde España el único modo de encontrarlo por texto era
    conocer ya su identificador `0P…`. Se reintenta sólo cuando el filtro no ha
    dicho nada: lo que él ordena bien sigue ordenado como él lo ordena."""
    llamadas = _con_llamadas(monkeypatch, [
        {"quotes": []},
        {"quotes": [_fondo_bbva()]},
    ])

    res = D._yahoo_search("BBVA Plan Megatendencia")

    assert res and res[0]["name"] == "BBVA Plan Megatendencia Tecnología PP"
    assert len(llamadas["urls"]) == 2, llamadas["urls"]
    assert "region=ES" in llamadas["urls"][0]
    assert "region=ES" not in llamadas["urls"][1]


def test_una_búsqueda_que_ya_dice_algo_no_paga_dos_peticiones(monkeypatch):
    """El reintento es una excepción, no un modo: duplicar cada búsqueda para
    volver a pedir lo mismo sería pagar dos veces lo que Yahoo acaba de dar."""
    llamadas = _con_llamadas(monkeypatch, [{"quotes": [_fondo_bbva()]}])

    D._yahoo_search("0P000011YA")

    assert len(llamadas["urls"]) == 1, llamadas["urls"]


# ── la entrada ────────────────────────────────────────────────────────────
def test_pegar_el_identificador_0P_trae_el_nombre(monkeypatch):
    """`docs/BUSCADOR.md` dice «pega su identificador de Morningstar (0P…) y se
    apunta igual». Apuntarse sin nombre no es apuntarse igual: el movimiento
    queda con el código en la casilla de nombre y el mapa lo enseña como
    «fuera del mapa» con un rótulo que no dice nada."""
    monkeypatch.setattr(D, "_yahoo_search", lambda q, limit=12: [
        {"symbol": "0P000011YA.F", "kind": "Fondo",
         "name": "BBVA Plan Megatendencia Tecnología PP", "exchange": "Frankfurt"}])

    esperado = ("0P000011YA.F", "BBVA Plan Megatendencia Tecnología PP", "Fondo")
    assert D._resolve_symbol("0P000011YA.F") == esperado
    assert D._resolve_symbol("0p000011ya") == esperado      # sin la plaza


def test_un_identificador_0P_desconocido_no_reventa(monkeypatch):
    """Si Yahoo no lo conoce, el código se queda tal cual —igual que antes— y
    sin nombre: la ficha no puede inventar uno."""
    monkeypatch.setattr(D, "_yahoo_search", lambda q, limit=12: [])

    assert D._resolve_symbol("0P0000ZZZZ.F") == ("0P0000ZZZZ.F", "", "")


# ── el formulario ─────────────────────────────────────────────────────────
def _estatico(nombre):
    r = D.app.test_client().get("/static/" + nombre)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _es_comentario(linea):
    return linea.lstrip().startswith(("//", "*", "/*"))


def _bloque(js, ancla):
    """Las líneas del bloque que abre `ancla`, hasta el cierre a su misma sangría.

    Por SANGRÍA y no contando llaves — contar llaves obliga a saber de cadenas
    de plantilla anidadas y equivocarse devuelve un trozo cortado sin avisar,
    con lo que cada `assert X in trozo` degenera en una búsqueda por todo el
    fichero que se aprueba sola. El ancla se busca fuera de comentarios.
    """
    lineas = js.split("\n")
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


def _linea_con(codigo, ancla):
    for l in codigo.split("\n"):
        if ancla in l and not _es_comentario(l):
            return l
    raise AssertionError(f"ninguna línea de código lleva {ancla!r}")


def test_elegir_un_resultado_de_yahoo_guarda_el_nombre():
    """El formulario sólo manda `name` cuando `sel` coincide con el símbolo
    tecleado. Un resultado de Yahoo al que no se le puede hacer clic deja `sel`
    a null, y entonces el movimiento se apunta con el nombre vacío — que es
    exactamente el movimiento que abrió este informe."""
    js = _estatico("cartera.js")

    cuerpo = _bloque(js, "function pick(x)")
    assert "sel = { symbol: x.symbol, name: x.name, kind: x.kind }" in cuerpo, cuerpo
    assert "fTk.value = x.symbol" in cuerpo, cuerpo

    # Y tiene que seguir habiendo quien lo llame desde el bloque de Yahoo: la
    # lista pinta `data-yh` en cada fila, y cada fila se entrega a `pick`.
    assert 'querySelectorAll("[data-yh]")' in js
    llamada = _linea_con(js, "() => pick(otros[")
    assert "addEventListener(\"click\"" in llamada, llamada
