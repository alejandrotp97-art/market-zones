"""El catálogo local: ingesta, búsqueda y las rutas que lo sirven.

Nada de red. La ingesta se prueba desde un ZIP construido en memoria, y la
búsqueda contra la base que esa ingesta acaba de escribir — no contra una
maqueta, porque la mitad de lo que puede fallar aquí es el SQL.
"""
import contextlib
import io
import os
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D
import ingest_firds as ING

NS = "urn:iso:std:iso:20022:tech:xsd:auth.017.001.02"

FONDOS = [
    ("IE00B4L5Y983", "iShares III Plc - iShares Core MSCI World UCITS ETF", "CEOGES", "USD",
     ["XETR", "MTAA", "XAMS"]),
    ("IE00B4L5Y983", "iShsIII-Core MSCI World U.ETF Reg.Shs USD (Acc) o.N.", "CEOGES", "USD",
     ["XETR"]),
    ("IE00BK5BQT80", "Vanguard FTSE All-World UCITS ETF (USD) Accumulating", "CEOGES", "USD",
     ["XETR", "MTAA"]),
    ("IE00B3RBWM25", "Vanguard FTSE All-World UCITS ETF Distributing", "CEOIES", "USD",
     ["XETR"]),
    ("LU0055649056", "DWS Gold Plus", "CIOIES", "EUR", ["XFRA"]),
    # Un fondo NO cotizado de los que se contratan con la gestora, listado sólo
    # en Stuttgart: es como el registro alcanza a la mitad «no cotizada».
    ("IE00BYX5NX33", "Fidelity MSCI World Index Fund - P EUR ACC", "CIOGES", "EUR", ["STUA"]),
    ("FR0010315770", "Amundi MSCI World Swap UCITS ETF Dist", "CEOIES", "EUR", ["XPAR"]),
    # Deuda: un ETC de oro, uno apalancado, y un bono corriente que NO debe entrar.
    ("FR0013416716", "Amundi Physical Gold ETC", "DEMYRT", "EUR", ["XPAR", "XETR"]),
    ("IE00BZ3XGLD1", "Gestora Delta Gold 3x Daily Leveraged ETP", "DAZSGR", "USD", ["XLON"]),
    ("US278058DV11", "ETN  3.950  3/6/29 c29 EATON CORP", "DBFGGR", "USD", ["XNYS"]),
]


def _xml():
    regs = []
    for isin, name, cfi, ccy, mics in FONDOS:
        for mic in mics:
            regs.append(
                f'<RefData xmlns="{NS}"><FinInstrmGnlAttrbts><Id>{isin}</Id>'
                f"<FullNm>{name}</FullNm><ClssfctnTp>{cfi}</ClssfctnTp>"
                f"<NtnlCcy>{ccy}</NtnlCcy></FinInstrmGnlAttrbts><Issr>LEI123</Issr>"
                f"<TradgVnRltdAttrbts><Id>{mic}</Id>"
                "<FrstTradDt>2019-05-23T05:00:00Z</FrstTradDt></TradgVnRltdAttrbts></RefData>")
    return (f'<?xml version="1.0" encoding="UTF-8"?><BizData xmlns="{NS}">'
            + "".join(regs) + "</BizData>").encode()


@pytest.fixture
def catalogo(tmp_path, monkeypatch):
    """Un catálogo de verdad, ingerido igual que el real, con seis fondos."""
    z = tmp_path / "FULINS_C_20260822_01of01.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("FULINS_C_20260822_01of01.xml", _xml())
    db = str(tmp_path / "instruments.db")
    ING.ingerir([str(z)], {"source": "prueba", "published": "2026-08-22"}, db)
    monkeypatch.setattr(D, "CATALOGO_DB", db)
    return db


# ── ingesta ───────────────────────────────────────────────────────────────
def test_la_ingesta_dobla_las_lineas_en_fondos(catalogo):
    # 6 vehículos colectivos + 2 ETC. El bono de EATON no cuenta.
    assert D._catalogo_meta()["funds"] == "8"
    assert D._catalogo_meta()["published"] == "2026-08-22"


def test_la_misma_plaza_repetida_no_se_cuenta_dos_veces(catalogo):
    """IWDA llega con XETR dos veces, en dos nombres distintos. Es la misma línea
    vista dos veces, no dos sitios donde comprar."""
    mics = [x["mic"] for x in D._catalogo_plazas("IE00B4L5Y983")]

    assert sorted(mics) == ["MTAA", "XAMS", "XETR"]


def test_el_catalogo_se_reemplaza_entero_o_nada(tmp_path):
    """Se escribe en un temporal y se renombra encima. Si la ingesta revienta a
    medias, el panel tiene que seguir leyendo el catálogo viejo — no uno con la
    mitad de los fondos, que no se distingue de uno completo."""
    db = tmp_path / "instruments.db"
    db.write_bytes(b"catalogo viejo")
    roto = tmp_path / "roto.zip"
    with zipfile.ZipFile(roto, "w") as zf:
        zf.writestr("x.xml", b"<esto no es> FIRDS")

    with pytest.raises(ET.ParseError):
        ING.ingerir([str(roto)], {"source": "prueba"}, str(db))

    assert db.read_bytes() == b"catalogo viejo"
    assert not list(tmp_path.glob(".instruments-*"))     # sin restos


# ── búsqueda ──────────────────────────────────────────────────────────────
def test_se_busca_por_nombre_comercial(catalogo):
    r = D._catalogo_buscar("vanguard all world")

    assert {x["isin"] for x in r} == {"IE00BK5BQT80", "IE00B3RBWM25"}


def test_se_busca_tambien_por_el_nombre_abreviado(catalogo):
    """Quien copie el nombre que trae su bróker tiene que encontrarlo igual. Por
    eso el índice lleva TODAS las variantes y no sólo la que se enseña."""
    r = D._catalogo_buscar("iShsIII Core")

    assert [x["isin"] for x in r] == ["IE00B4L5Y983"]
    assert r[0]["name"] == "iShares Core MSCI World UCITS ETF"   # lo que se ENSEÑA


def test_se_busca_por_ISIN(catalogo):
    assert [x["isin"] for x in D._catalogo_buscar("IE00BK5BQT80")] == ["IE00BK5BQT80"]


def test_se_busca_mientras_se_escribe(catalogo):
    """La última palabra va como prefijo: sin eso, «vangua» no encuentra nada
    hasta terminar de teclear, que es justo cuando ya no hace falta buscar."""
    assert D._catalogo_buscar("vangua")


def test_el_filtro_de_reparto_separa_las_dos_clases(catalogo):
    """Es la razón principal para buscar un fondo europeo, y Yahoo no la sabe:
    el mismo producto tiene una clase que acumula y otra que reparte."""
    acc = D._catalogo_buscar("vanguard all world", dist="acumulación")
    dis = D._catalogo_buscar("vanguard all world", dist="distribución")

    assert [x["isin"] for x in acc] == ["IE00BK5BQT80"]
    assert [x["isin"] for x in dis] == ["IE00B3RBWM25"]


def test_el_filtro_de_tipo_separa_ETF_de_fondo(catalogo):
    assert not D._catalogo_buscar("DWS Gold", kind="ETF")
    assert D._catalogo_buscar("DWS Gold", kind="Fondo")


def test_el_filtro_de_domicilio(catalogo):
    assert [x["isin"] for x in D._catalogo_buscar("world", domicile="fr")] == ["FR0010315770"]


def test_manda_el_que_cotiza_en_mas_plazas(catalogo):
    """`bm25` a secas premia al documento más corto, que aquí es el fondo con
    menos nombres alternativos: al revés de lo que interesa. Un fondo con
    cuarenta plazas es el grande, y casi siempre el que se estaba buscando."""
    r = D._catalogo_buscar("msci world")

    assert r[0]["isin"] == "IE00B4L5Y983"     # 3 plazas, frente a 1 del de Amundi


# ── lo que NO se afirma ───────────────────────────────────────────────────
def test_el_catalogo_no_dice_la_divisa_de_cada_plaza(catalogo):
    """La divisa que publica el registro es la de DENOMINACIÓN del fondo, y es
    una sola. En cuál se cruza en Milán no lo dice nadie hasta pedir el precio,
    y hasta entonces no se afirma."""
    r = D._catalogo_buscar("iShares Core MSCI World")[0]

    assert r["ccy"] == "USD"
    assert all("ccy" not in p for p in D._catalogo_plazas(r["isin"]))


def test_sin_catalogo_la_busqueda_calla_en_vez_de_reventar(tmp_path, monkeypatch):
    """El catálogo lo construye un temporizador semanal. Hasta la primera vez no
    existe, y el alta manual tiene que funcionar igual: la pantalla cae al
    buscador de Yahoo. Una base sin construir no puede tumbar el formulario."""
    monkeypatch.setattr(D, "CATALOGO_DB", str(tmp_path / "no-existe.db"))

    assert D._catalogo_buscar("msci world") == []
    assert D._catalogo_plazas("IE00B4L5Y983") == []
    assert D._catalogo_meta() == {}


def test_el_texto_del_usuario_nunca_es_sintaxis_de_FTS(catalogo):
    """FTS5 tiene lenguaje propio: comillas, `AND`, `NOT`, `*`, `^`. Pasarle el
    texto tal cual convertiría una comilla suelta en un error de sintaxis y un
    `NOT` escrito sin querer en un filtro que nadie pidió."""
    for veneno in ['msci" OR "', "world*^^", '"', "(((", "^vanguard"]:
        D._catalogo_buscar(veneno)            # no revienta

    # Las comillas se caen y lo demás se busca igual.
    assert D._catalogo_buscar('vanguard" "all') == D._catalogo_buscar("vanguard all")
    # Y un operador escrito sin querer es una PALABRA, no un operador: «NOT
    # vanguard» busca las dos, no «todo menos vanguard». Aquí no hay ningún fondo
    # que se llame «not», así que no sale ninguno — y sobre todo, no salen todos
    # los demás, que es lo que haría una negación colada.
    assert D._catalogo_buscar("NOT vanguard") == []


# ── rutas ─────────────────────────────────────────────────────────────────
@pytest.fixture
def cli(catalogo):
    return D.app.test_client()


def test_la_ruta_de_busqueda_devuelve_la_procedencia(cli):
    """Un catálogo que no dice cuándo se llenó es un catálogo en el que hay que
    confiar a ciegas — y éste se queda parado si el temporizador semanal falla."""
    d = cli.get("/api/instruments/search?q=vanguard").get_json()

    assert len(d["results"]) == 2
    assert d["source"]["published"] == "2026-08-22"


def test_la_ruta_no_busca_con_una_sola_letra(cli):
    assert cli.get("/api/instruments/search?q=v").get_json()["results"] == []


def test_la_ruta_de_plazas(cli):
    d = cli.get("/api/instruments/plazas?isin=IE00B4L5Y983").get_json()

    assert len(d["listings"]) == 3


def test_resolver_exige_un_ISIN(cli):
    assert "error" in cli.get("/api/instruments/resolver?isin=SPY").get_json()


@contextlib.contextmanager
def _cubo(fichas: float):
    """Deja el limitador con las fichas justas, y lo devuelve como estaba."""
    tokens, ts = D._upstream._tokens, D._upstream._ts
    D._upstream._tokens, D._upstream._ts = fichas, time.monotonic()
    try:
        yield
    finally:
        D._upstream._tokens, D._upstream._ts = tokens, ts


def test_un_ISIN_mal_formado_no_gasta_presupuesto(cli):
    """Rechazar la forma no cuesta red, así que tampoco puede costar ficha.

    Si cobrase, bastaría con pedir basura para vaciar el cubo y dejar sin
    presupuesto a las búsquedas de verdad.
    """
    with _cubo(0.0):
        r = cli.get("/api/instruments/resolver?isin=SPY")

    assert r.status_code == 200
    assert "error" in r.get_json()


def test_el_resolver_paga_el_limitador(cli, monkeypatch):
    """Es el camino más caro del servicio: hasta 19 peticiones a Yahoo por
    llamada, en serie. Sin freno un bucle las multiplica sin techo; con él, la
    siguiente del tirón recibe un 429 que además dice cuánto esperar."""
    _montar(monkeypatch, ("FR0013416716.SG", "EUR", 158.12, True))

    with _cubo(1.0):
        primera = cli.get("/api/instruments/resolver?isin=FR0013416716")
        segunda = cli.get("/api/instruments/resolver?isin=FR0013416716")

    assert primera.status_code == 200
    assert segunda.status_code == 429
    assert int(segunda.headers["Retry-After"]) >= 1


def test_el_resolver_no_puede_ocupar_el_pool_entero(cli, monkeypatch):
    """El limitador mide el RITMO, no los hilos ocupados.

    waitress sirve con un pool fijo de ocho. Sin este tope, ocho resoluciones
    en vuelo dejan el panel entero —zonas, régimen, cartera— sin un solo hilo
    libre mientras Yahoo tarda.
    """
    _montar(monkeypatch, ("FR0013416716.SG", "EUR", 158.12, True))
    tomados = [D._resolver_slots.acquire(blocking=False)
               for _ in range(D._RESOLVER_A_LA_VEZ)]
    try:
        assert all(tomados)
        r = cli.get("/api/instruments/resolver?isin=FR0013416716")
    finally:
        for _ in tomados:
            D._resolver_slots.release()

    assert r.status_code == 429


def _yahoo(*pares):
    """Simula a Yahoo: (símbolo, divisa, precio, ¿tiene serie?)."""
    porsym = {s: (c, p, h) for s, c, p, h in pares}
    return (lambda q, limit=12: [{"symbol": s, "name": "x", "kind": "ETF"} for s in porsym],
            lambda s: (porsym[s][1], porsym[s][0]),
            lambda s: list(range(40)) if porsym[s][2] else None)


def _montar(monkeypatch, *pares):
    buscar, meta, serie = _yahoo(*pares)
    monkeypatch.setattr(D, "_yahoo_search", buscar)
    monkeypatch.setattr(D, "_quote_meta", meta)
    monkeypatch.setattr(D, "_close_series", serie)


def test_resolver_prefiere_la_linea_en_LA_DIVISA_DE_LA_CARTERA(cli, monkeypatch):
    """Medido con el oro de Amundi: Yahoo devuelve `GOLD.AS` a 184,72 USD y
    `FR0013416716.SG` a 158,12 EUR, en ese orden. Quedarse con la primera que
    conteste apunta el precio en la divisa equivocada — y eso ya partió una
    posición en dos."""
    _montar(monkeypatch, ("GOLD.AS", "USD", 184.72, True),
                         ("FR0013416716.SG", "EUR", 158.12, True))

    d = cli.get("/api/instruments/resolver?isin=FR0013416716").get_json()

    assert d["best"] == "FR0013416716.SG"
    assert d["base_ccy"] == "EUR"


def test_manda_la_divisa_de_la_CARTERA_y_no_la_de_denominacion(cli, monkeypatch):
    """No son lo mismo, y confundirlas engaña: el registro dice que el VanEck de
    uranio está DENOMINADO en USD, y en Stuttgart se compra en euros. Quien mira
    esta pantalla compra en euros."""
    _montar(monkeypatch, ("NUCL.SW", "CHF", 44.4, True),
                         ("IE000M7V94E1.SG", "EUR", 47.3, True))

    d = cli.get("/api/instruments/resolver?isin=IE000M7V94E1").get_json()

    assert d["best"] == "IE000M7V94E1.SG"


def test_a_igualdad_de_divisa_gana_la_que_se_puede_dibujar(cli, monkeypatch):
    _montar(monkeypatch, ("SINSERIE.SG", "EUR", 10.0, False),
                         ("CONSERIE.MI", "EUR", 10.0, True))

    d = cli.get("/api/instruments/resolver?isin=FR0013416716").get_json()

    assert d["best"] == "CONSERIE.MI"


def test_se_ofrecen_TODAS_las_lineas_que_sirven(cli, monkeypatch):
    """Cuál es la tuya no lo sabe ni el registro ni Yahoo: lo sabes tú, que la
    compraste. Se sugiere una y se enseñan las demás."""
    _montar(monkeypatch, ("GOLD.AS", "USD", 184.72, True),
                         ("FR0013416716.SG", "EUR", 158.12, True))

    d = cli.get("/api/instruments/resolver?isin=FR0013416716").get_json()

    assert {o["symbol"] for o in d["options"]} == {"GOLD.AS", "FR0013416716.SG"}
    assert {o["ccy"] for o in d["options"]} == {"EUR", "USD"}


def test_una_linea_sin_serie_se_declara(cli, monkeypatch):
    """Hay líneas que cotizan y no se pueden dibujar: Yahoo da el precio de hoy y
    ni un día de serie. Se valoran igual, pero quien la añade tiene derecho a
    saberlo ANTES — cuando se ve la gráfica vacía ya está apuntado."""
    _montar(monkeypatch, ("X.SG", "EUR", 12.0, False))
    monkeypatch.setattr(D, "HISTORY_PROXY", {})

    d = cli.get("/api/instruments/resolver?isin=FR0013416716").get_json()

    assert d["options"][0]["history"] is False


def test_una_linea_con_proxy_comprobado_SI_se_puede_dibujar(cli, monkeypatch):
    """`HISTORY_PROXY` es una tabla comprobada a mano. Avisar de «sin histórico»
    sobre una línea que el panel pinta perfectamente sería asustar sin motivo."""
    _montar(monkeypatch, ("X.SG", "EUR", 12.0, False))
    monkeypatch.setattr(D, "HISTORY_PROXY", {"X.SG": "X.MI"})

    o = cli.get("/api/instruments/resolver?isin=FR0013416716").get_json()["options"][0]

    assert o["history"] is True
    assert o["via"] == "X.MI"


def test_una_linea_que_no_cotiza_ni_se_ofrece(cli, monkeypatch):
    """Ofrecerla apuntaría un movimiento que la cartera no sabría valorar nunca, y
    el fallo aparecería semanas después."""
    monkeypatch.setattr(D, "_yahoo_search",
                        lambda q, limit=12: [{"symbol": "X.SG", "name": "X", "kind": "ETF"}])
    monkeypatch.setattr(D, "_quote_meta", lambda s: (None, None))

    d = cli.get("/api/instruments/resolver?isin=FR0013416716").get_json()

    assert "error" in d and "options" not in d


# ── ETC ───────────────────────────────────────────────────────────────────
def test_el_oro_fisico_esta_en_el_catalogo(catalogo):
    """Un ETC es jurídicamente deuda y vive en otro fichero del registro. Sin
    ingerirlo, el buscador no encontraba el oro — que es de lo primero que se
    busca en un panel de cartera."""
    r = D._catalogo_buscar("physical gold")

    assert "FR0013416716" in {x["isin"] for x in r}
    oro = next(x for x in r if x["isin"] == "FR0013416716")
    assert oro["kind"] == "ETC"
    assert oro["leveraged"] == 0


def test_un_bono_corporativo_NO_entra_aunque_diga_ETN(catalogo):
    """«ETN 3.950 3/6/29 c29 EATON CORP» es un bono de EATON, cuyo ticker es ETN.
    Medido: 79 de los 988 ISIN de deuda con esa palabra en el nombre son suyos.
    Un buscador de fondos que ofrece deuda corporativa está roto."""
    assert D._catalogo_buscar("EATON") == []
    assert D._catalogo_buscar("ETN 3.950") == []


def test_el_apalancado_se_marca_pero_no_se_esconde(catalogo):
    """Dos tercios de los ETC del registro son apalancados o inversos, y se
    llaman casi igual que el producto físico. Esconderlos rompería el poder
    apuntar uno que ya tengas; no marcarlos sería una trampa."""
    r = D._catalogo_buscar("gestora delta gold")

    assert [x["isin"] for x in r] == ["IE00BZ3XGLD1"]
    assert r[0]["leveraged"] == 1


def test_se_puede_filtrar_solo_ETC(catalogo):
    assert {x["isin"] for x in D._catalogo_buscar("gold", kind="ETC")} \
        == {"FR0013416716", "IE00BZ3XGLD1"}


def test_el_ETC_no_finge_una_politica_de_reparto(catalogo):
    """La cuarta letra del CFI dice acumula/reparte en los vehículos colectivos.
    En deuda esa posición significa otra cosa, así que aquí no se traduce: `None`
    es «el registro no lo dice» y es la respuesta correcta."""
    oro = D._catalogo_buscar("physical gold")[0]

    assert oro["dist_policy"] is None


# ── que un tipo no tape a los demás ───────────────────────────────────────
def test_los_ETF_no_pueden_tapar_a_los_fondos(catalogo):
    """Sin cupo por tipo, «msci world index» devolvía CINCO ETF y ni un fondo,
    habiendo fondos que encajaban mejor. Se probaron tres fórmulas de orden
    —lineal por plazas, logarítmica y sólo-en-cotizados— y las tres daban lo
    mismo: hay muchos más ETF y un fondo nunca gana un cupo global."""
    tipos = {x["kind"] for x in D._catalogo_buscar("msci world", limit=12)}

    assert "ETF" in tipos and "Fondo" in tipos


def test_cada_tipo_aparece_una_sola_vez_seguida(catalogo):
    """La pantalla pone un subtítulo cada vez que cambia el tipo. Si el orden los
    intercala, se pintan dos cabeceras «ETFs» con una de «Fondos» en medio y
    parece un fallo aunque el contenido sea correcto."""
    r = D._catalogo_buscar("gold", limit=12)
    bloques = [x["kind"] for i, x in enumerate(r) if i == 0 or r[i - 1]["kind"] != x["kind"]]

    assert len(bloques) == len(set(bloques))


def test_el_relleno_no_devuelve_lo_que_el_cupo_limitaba(catalogo):
    """La cola que completa la lista es SÓLO para los tipos sin cupo. Dejarla
    traer más ETF devolvía el problema entero: al agrupar se ponían delante y
    empujaban a los fondos fuera del corte."""
    r = D._catalogo_buscar("world", limit=12)
    etfs = [x for x in r if x["kind"] == "ETF"]

    assert len(etfs) <= 4                      # el cupo, no la lista entera


def test_filtrar_por_tipo_sigue_dando_solo_ese_tipo(catalogo):
    r = D._catalogo_buscar("world", kind="Fondo", limit=10)

    assert r and all(x["kind"] == "Fondo" for x in r)


def test_un_fondo_no_cotizado_del_registro_se_encuentra_y_se_valora(catalogo, monkeypatch):
    """El registro alcanza a los fondos que alguna gestora sí lista en un mercado
    alemán, y Yahoo los conoce por su identificador de Morningstar. Es la mitad
    de la Fase 2 que sale gratis con la Fase 1 ya construida."""
    assert [x["isin"] for x in D._catalogo_buscar("fidelity msci world index")] \
        == ["IE00BYX5NX33"]

    _montar(monkeypatch, ("0P0001CLDK.F", "EUR", 14.03, True),
                         ("IE00BYX5NX33.SG", "EUR", 13.90, False))
    d = D.app.test_client().get(
        "/api/instruments/resolver?isin=IE00BYX5NX33").get_json()

    assert d["best"] == "0P0001CLDK.F"          # el que TIENE serie, a igual divisa


# ── fondos de gestora directa (Morningstar, en vivo) ──────────────────────
# El registro de la UE no los recoge: no cotizan en ningún mercado. Se consultan
# EN VIVO y no se copia nada, así que aquí se prueba el parseo y el cableado —la
# red la bloquea `conftest.py` con `MZ_NO_NETWORK=1`.
FILA_MS = {"SecId": "F0GBR04AZ2", "Name": "Bestinver Internacional FI",
           "isin": "ES0114638036", "PerformanceId": "0P00000P2M",
           "OngoingCharge": 1.83, "categoryName": "RV Europa Cap. Flexible"}


def _ms(monkeypatch, *filas):
    import json as _json
    D._ms_cache.clear()          # la caché vive 5 min y falsearía el test siguiente
    monkeypatch.setattr(D.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(_json.dumps({"rows": list(filas)}).encode()))


def test_un_fondo_de_gestora_trae_su_identificador_y_sus_gastos(monkeypatch):
    """Las tres cosas que hacen falta y que el registro de la UE no da: el ISIN,
    el `0P…` con el que Yahoo sabe ponerle precio, y los gastos corrientes."""
    _ms(monkeypatch, FILA_MS)

    r = D._morningstar_buscar("bestinver internacional")

    assert r == [{"isin": "ES0114638036", "perf_id": "0P00000P2M",
                  "name": "Bestinver Internacional FI",
                  "category": "RV Europa Cap. Flexible", "ter": 1.83}]


def test_unos_gastos_a_cero_son_gastos_QUE_NO_PUBLICAN(monkeypatch):
    """Salen a 0 en fondos que sí cobran. Enseñar «gastos 0,00%» sería una
    afirmación falsa sobre dinero, y de las que no se notan."""
    _ms(monkeypatch, {**FILA_MS, "OngoingCharge": 0})

    assert D._morningstar_buscar("cobas")[0]["ter"] is None


def test_sin_identificador_no_se_ofrece(monkeypatch):
    """Sin `0P…` la cartera no sabría valorarlo, y ofrecerlo apuntaría un
    movimiento que nunca tendría precio."""
    _ms(monkeypatch, {**FILA_MS, "PerformanceId": ""}, {**FILA_MS, "isin": ""})

    assert D._morningstar_buscar("lo que sea") == []


def test_si_la_fuente_de_apoyo_falla_la_busqueda_no_se_rompe(monkeypatch):
    """Es una fuente de APOYO: la principal es el registro oficial. Un buscador
    que revienta porque un tercero está caído es peor que uno que encuentra
    menos."""
    def revienta(*a, **k):
        raise OSError("Morningstar no contesta")
    D._ms_cache.clear()
    monkeypatch.setattr(D.urllib.request, "urlopen", revienta)

    assert D._morningstar_buscar("bestinver") == []


def test_una_caida_pasajera_NO_se_cachea(monkeypatch):
    """Cachear el vacío de un fallo dejaría la búsqueda muerta cinco minutos
    después de que la fuente volviera."""
    D._ms_cache.clear()
    monkeypatch.setattr(D.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("caída")))
    assert D._morningstar_buscar("bestinver") == []

    _ms(monkeypatch, FILA_MS)
    assert len(D._morningstar_buscar("bestinver")) == 1     # a la siguiente, va


def test_no_se_consulta_por_dos_letras(monkeypatch):
    """Con dos letras contestaría medio universo y sería una petición a una base
    ajena por cada tecla."""
    _ms(monkeypatch, FILA_MS)

    assert D._morningstar_buscar("be") == []


def test_el_resolvedor_encuentra_el_fondo_por_su_0P(monkeypatch, catalogo):
    """Es el ÚNICO camino: Yahoo no indexa estos fondos por nombre —medido, cero
    de dieciocho— aunque sepa ponerles precio por ese identificador."""
    pedidos = []

    def espia(q, limit=12):
        pedidos.append(q)
        return [{"symbol": "0P00000P2M.F", "name": "Bestinver", "kind": "Fondo"}] \
            if q == "0P00000P2M" else []

    monkeypatch.setattr(D, "_yahoo_search", espia)
    monkeypatch.setattr(D, "_quote_meta", lambda s: (79.60, "EUR"))
    monkeypatch.setattr(D, "_close_series", lambda s: list(range(40)))

    d = D._catalogo_resolver("ES0114638036", perf_id="0P00000P2M",
                             nombre="Bestinver Internacional FI")

    assert d["best"] == "0P00000P2M.F"
    assert pedidos[0] == "0P00000P2M"          # el identificador va PRIMERO


# ── regresiones de la revisión ────────────────────────────────────────────
def test_la_busqueda_por_cupo_no_deja_conexiones_abiertas(catalogo, monkeypatch):
    """La rama del cupo salía por `return` sin cerrar la conexión que había
    abierto. Medido contra el servicio: 40 búsquedas, 40 descriptores colgados.
    Un servidor que pierde un recurso por tecleo se muere solo.

    Se comprueba usándolas: una conexión cerrada levanta `ProgrammingError`, así
    que si alguna sigue respondiendo es que nadie la cerró."""
    abiertas = []
    real = D._catalogo_conn

    def contando():
        c = real()
        if c is not None:
            abiertas.append(c)
        return c

    monkeypatch.setattr(D, "_catalogo_conn", contando)
    for _ in range(5):
        D._catalogo_buscar("world")           # sin filtro: entra por el cupo
        D._catalogo_buscar("world", kind="ETF")

    assert abiertas, "el test no probó nada: no se abrió ninguna conexión"
    vivas = []
    for c in abiertas:
        try:
            c.execute("SELECT 1")
            vivas.append(c)
        except sqlite3.ProgrammingError:
            pass                              # cerrada, que es lo que se espera

    assert vivas == []


def test_un_limite_no_numerico_no_tumba_la_ruta(cli):
    """`int('abc')` sin guardia devolvía un 500. Un parámetro mal escrito no es
    un fallo del servidor: se trata como si no viniera."""
    assert cli.get("/api/instruments/search?q=world&limit=abc").status_code == 200
    assert cli.get("/api/instruments/search?q=world&limit=-5").status_code == 200
    assert cli.get("/api/instruments/search?q=world&limit=99999").status_code == 200


def test_el_limite_por_defecto_es_el_que_dice_la_documentacion(catalogo):
    """El documento decía que se bajó a 12 y el defecto del servidor seguía en
    20: quedaba arreglado sólo para la pantalla, y roto para cualquier otro."""
    assert D._LIMITE_POR_DEFECTO == 12
    assert len(D._catalogo_buscar("world")) <= 12
