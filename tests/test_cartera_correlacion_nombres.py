"""La tarjeta de diversificación se rotula por NOMBRE, no por el símbolo.

Un fondo cotiza en el panel como `0P0001CLDK.F`. Lo malo no es que sea feo: la
cabecera de la matriz recortaba el símbolo a seis caracteres y dos posiciones
distintas salían las dos como `0P0001`. Una tabla con dos columnas del mismo
rótulo no se puede leer —no dice de menos, dice de más y mal—. Y la lista contra
el índice pisaba el número con el símbolo porque su columna estaba clavada al
ancho de un código de divisa de tres letras, que es para lo que nació.

Los ayudantes de lectura estática se copian de `test_cartera_layout.py` en vez
de importarse de allí. Aquel fichero fija el ARMAZÓN de la página —qué vive en
qué columna, que el mapa sea uno solo— y éste, cómo se rotula UNA tarjeta. Son
dos preguntas distintas y tienen que poder fallar por separado.

Aquí tampoco se comprueba que exista una cadena en un fichero: se comprueba el
TROZO de código que hace el trabajo. Una aserción que sigue en verde después de
borrar lo que dice vigilar no vigila nada.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


def _estatico(nombre):
    r = D.app.test_client().get("/static/" + nombre)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _es_comentario(linea):
    return linea.lstrip().startswith(("//", "*", "/*"))


def _bloque(js, ancla):
    """Las líneas del bloque que abre `ancla`, hasta el cierre a su misma sangría.

    Por SANGRÍA y no contando llaves: contar llaves obliga a saber de cadenas de
    plantilla anidadas —de las que esta función está llena— y equivocarse
    devuelve un trozo cortado sin avisar, con lo que cada `assert X in trozo`
    degenera en una búsqueda por todo el fichero que se aprueba sola.
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


def _solo_codigo(bloque):
    """El bloque sin comentarios. Los de aquí explican lo que hace el código y
    por tanto nombran las mismas cosas: buscarlas sobre el bloque entero acaba
    señalando la explicación y la comprobación no puede fallar nunca."""
    return "\n".join(l for l in bloque.split("\n") if not _es_comentario(l))


def _linea_con(codigo, ancla):
    for l in codigo.split("\n"):
        if ancla in l:
            return l
    raise AssertionError(f"ninguna línea de código lleva {ancla!r}")


def _texto_pintado(linea, clase):
    """Lo que se PINTA dentro del elemento de esa clase.

    Mirar la línea entera confunde el rótulo con el `title`, que desde el
    arreglo del recorte lleva también el nombre. Son dos cosas distintas: uno se
    lee de un vistazo y el otro hay que ir a buscarlo con el ratón, y una
    aserción que no las separa se aprueba con el título mientras el rótulo
    visible vuelve al símbolo.
    """
    m = re.search(r'<\w+ class="[^"]*\b' + re.escape(clase) + r'\b[^"]*"[^>]*>([^<]*)<', linea)
    assert m, f"no encuentro qué se pinta dentro de .{clase}"
    return m.group(1)


def _regla(css, selector):
    """El cuerpo de la ÚNICA regla con ese selector, anclada a principio de línea.

    El ancla, porque sin ella preguntar por `.ccy-row` devuelve el cuerpo de
    `.corr-vs .ccy-row`, que es justo la excepción que se quiere comparar
    contra la regla general.

    La unicidad, porque el navegador resuelve por CASCADA y se queda con la
    ÚLTIMA declaración: si el mismo selector se escribiera dos veces, este
    ayudante seguiría afirmando cosas sobre la primera mientras manda la otra.
    """
    cuerpos = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert cuerpos, f"no existe la regla {selector}"
    assert len(cuerpos) == 1, f"{selector} está escrita {len(cuerpos)} veces; manda la última"
    return cuerpos[0]


def _selectores_con_clase(css, clase):
    """Todos los selectores de la hoja que alcanzan a esa clase.

    Preguntar sólo por la regla propia no basta: el estilo efectivo es la
    CASCADA de todas las reglas que casan, y una regla suelta escrita en otra
    parte del fichero llega igual. Es exactamente lo que hay que poder ver.
    """
    return re.findall(r"(?m)^([^{}\n]*\." + re.escape(clase) + r"\b[^{}\n]*?)\s*\{", css)


def _corr():
    return _solo_codigo(_bloque(_estatico("cartera.js"), "async function loadCorr()"))


# ── el rótulo ────────────────────────────────────────────────────────────
def test_la_tarjeta_no_recorta_el_simbolo_para_titular():
    """El defecto exacto. `t.slice(0, 6)` en la cabecera dejaba dos columnas
    llamadas `0P0001`, y `t.slice(0, 12)` en las filas cortaba justo por donde
    los fondos de una misma gestora se parecen. Recortar un identificador opaco
    no lo hace más legible: lo hace ambiguo, que es peor."""
    c = _corr()
    cabecera, fila = _linea_con(c, "<thead>"), _linea_con(c, 'class="sym"')

    assert ".slice(" not in cabecera, "la cabecera vuelve a recortar"
    assert ".slice(" not in fila, "la fila vuelve a recortar"
    # Y la red por debajo, para un recorte nuevo en cualquier otro rótulo. Va
    # DESPUÉS de las dos de arriba a propósito: una negación sobre un trozo mal
    # recortado se aprueba sola, mientras que las dos búsquedas positivas
    # revientan si `_bloque` deja de coger lo que cree.
    assert ".slice(" not in c, "hay un recorte nuevo en la tarjeta"


def test_cada_rotulo_sale_del_nombre_que_manda_el_servidor():
    """Los cuatro sitios donde la tarjeta nombra una posición —el ranking contra
    el índice, el par que más se parece, las filas de la matriz y la nota de lo
    que se queda fuera— tienen que decir el nombre. Uno solo que se quede con el
    símbolo obliga a traducir a mano dentro de la misma tarjeta."""
    c = _corr()

    assert "d.names" in c, "el nombre lo manda el servidor; aquí no se inventa"
    assert "nombre(mc.a)" in c and "nombre(mc.b)" in c, "el par que más se parece"
    # Sobre el texto que se PINTA, no sobre la línea entera: desde que el
    # `title` de la fila lleva también el nombre, buscar `nombre(` en la línea
    # se aprueba con el título aunque el rótulo visible vuelva al símbolo.
    for clase, sitio in (("ccy-n", "el ranking contra el índice"),
                         ("corr-nm", "las filas de la matriz")):
        pintado = _texto_pintado(_linea_con(c, f'class="{clase}"'), clase)
        assert "nombre(" in pintado, f"{sitio}: se pinta {pintado!r}"
    assert "x.name" in _linea_con(c, "d.excluded.map"), \
        "la nota de lo que se queda fuera"


def test_el_nombre_de_una_fila_es_el_de_SU_simbolo():
    """La correspondencia se hace por POSICIÓN entre dos listas paralelas. Si se
    construye recorriendo `d.names` en vez de `d.tickers`, o si el índice se
    pierde por el camino, cada fila queda rotulada con el nombre de otra —y una
    matriz mal rotulada no se ve rota: se ve tranquila y miente."""
    c = _corr()

    montaje = _linea_con(c, "rotulos[")
    assert "d.tickers" in montaje and "d.names" in montaje, (
        "el nombre se casa con SU símbolo recorriendo los símbolos")
    # El ÍNDICE, no sólo las dos listas. Con `[0]`, `[i + 1]` o el índice dado
    # la vuelta, las dos listas siguen nombradas en la línea y cada fila queda
    # rotulada con el nombre de otra: verde y mintiendo.
    assert "[i] || t" in montaje, (
        "el nombre se toma por el ÍNDICE del símbolo que se está recorriendo, "
        "y cae al símbolo si no hay ninguno apuntado")


def test_las_columnas_van_numeradas_como_las_filas():
    """Una cabecera con el nombre entero multiplica el ancho de la tabla por el
    número de posiciones, y recortarlo es volver al problema de partida. El
    número no se recorta y no se repite: la columna 2 es la fila 2."""
    c = _corr()

    assert "orden[t]" in _linea_con(c, "<thead>"), "la cabecera va numerada"
    assert "orden[t]" in _linea_con(c, 'class="sym"'), "y la fila lleva su número"


def test_el_simbolo_no_se_pierde_queda_a_mano_en_el_title():
    """El nombre es para leer la tarjeta; el símbolo es para casarla con el
    extracto del bróker. Cambiar uno por otro cambia de problema en vez de
    resolverlo, así que el símbolo se queda en el `title`."""
    c = _corr()

    for ancla in ('class="ccy-n"', 'class="sym"'):
        titulo = re.search(r'title="\$\{([^}]*)\}"', _linea_con(c, ancla))
        assert titulo, f"{ancla} se quedó sin `title`"
        # Se descuenta el nombre y tiene que QUEDAR el símbolo. Comprobar que
        # hay un `title` y parar ahí deja pasar un `title` que repita el nombre
        # ya visible, que es borrar justo lo que este test dice guardar.
        resto = titulo.group(1).replace("nombre(t)", "")
        assert re.search(r"(?<!\w)t(?!\w)", resto), (
            f"el `title` de {ancla} tiene que llevar el SÍMBOLO; "
            f"hoy sólo lleva {titulo.group(1)!r}")


# ── el ancho ─────────────────────────────────────────────────────────────
def test_el_nombre_no_cabe_en_el_ancho_de_un_codigo_de_divisa():
    """`.ccy-row` nació para `EUR`, `USD`, `GBP` y clava esa columna a 42px. Un
    nombre de fondo se salía por encima del número y los dos se leían pisados
    —que es como lo vio el usuario—. La lista contra el índice reutiliza la
    rejilla, así que tiene que pedir la suya para esa columna."""
    css = _estatico("cartera.css")

    base = _regla(css, ".ccy-row")
    assert "42px" in base, "si esto cambia, la razón de la excepción de abajo ya no existe"
    propia = _regla(css, ".corr-vs .ccy-row")
    assert "minmax(0,1fr)" in propia.replace(" ", ""), (
        "la columna del nombre se estira y puede encogerse; no lleva ancho fijo")
    assert "text-overflow:ellipsis" in _regla(css, ".corr-vs .ccy-n").replace(" ", "")


def test_un_nombre_muy_largo_no_empuja_la_matriz_fuera_de_la_pantalla():
    """La matriz crece a lo ancho con cada posición. Si además cada rótulo mide
    lo que mida el nombre, un fondo de sesenta caracteres se lleva la tabla
    entera de paseo. Se acota y se corta con puntos suspensivos, con el nombre
    completo en el `title`."""
    css = _estatico("cartera.css")

    nm = _regla(css, ".corr-tbl .corr-nm").replace(" ", "")
    assert "max-width:" in nm
    assert "white-space:nowrap" in nm, (
        "sin `nowrap` el nombre ENVUELVE dentro de la caja y el ellipsis no "
        "llega a pintarse nunca: ésa es la declaración que hace el recorte")
    assert "text-overflow:ellipsis" in nm
    assert "display:inline-block" in nm, (
        "en una tabla de ancho automático el `max-width` de la celda no se "
        "respeta; hace falta un elemento de línea dentro que sí lo respete")


def test_el_rotulo_de_la_fila_no_reutiliza_una_clase_que_ya_exista():
    """BUG REAL, y el que estos tests no cazaron a la primera.

    La primera versión llamó `nm` a la clase del rótulo. `.nm` YA existía suelta
    en la hoja —sin contenedor, con `white-space:normal` dentro—, así que la
    regla nueva ganaba en especificidad SÓLO en lo que declaraba: el
    `white-space` seguía llegando de la vieja, el nombre envolvía en vez de
    cortarse y el `text-overflow:ellipsis` no se pintaba nunca. De paso heredaba
    su color apagado y su tamaño.

    Y el test que lo vigilaba seguía VERDE, porque leía el cuerpo de UNA regla y
    el estilo efectivo es la cascada de TODAS las que casan.

    Por eso aquí no se pregunta por la regla propia. Se pregunta quién más
    alcanza a la clase: si algo que no es de esta tabla puede tocarla, el rótulo
    está a merced de una regla escrita para otra cosa.
    """
    css, js = _estatico("cartera.css"), _corr()

    clases = re.findall(r'<span class="([\w-]+)">', _linea_con(js, 'class="sym"'))
    assert clases, "la fila de la matriz dejó de rotularse con elementos propios"
    for clase in clases:
        alcanzan = _selectores_con_clase(css, clase)
        assert alcanzan, f"`.{clase}` se usa y no tiene ninguna regla"
        for sel in alcanzan:
            assert ".corr-tbl" in sel, (
                f"a `.{clase}` la alcanza también `{sel}`, que no es de esta "
                "tabla: el estilo efectivo sale de la CASCADA, no de la regla "
                "propia, y esa otra puede declarar lo que aquí se da por hecho")


def test_la_lista_contra_el_indice_pide_la_rejilla_que_se_le_escribio():
    """La regla y quien la usa van juntas o no van. Sin esta atadura, renombrar
    la clase en un lado deja al otro con CSS muerto y la lista vuelve a pisarse
    sin que ningún test se entere."""
    assert "corr-vs" in _linea_con(_corr(), "ccy-list")
    assert ".corr-vs" in _estatico("cartera.css")
