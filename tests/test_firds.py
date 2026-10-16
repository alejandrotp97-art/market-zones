"""El registro europeo, leído sin salir a la red.

FIRDS es un XML de 91 MB con 150.000 líneas de cotización. Aquí se prueba contra
un recorte de cinco registros construido a mano, con la misma forma exacta que el
fichero real —namespace incluido— porque la suite no puede descargar nada.

Lo que se fija es lo que se AFIRMA a partir del registro. Cada aserción sobre el
CFI está comprobada contra un instrumento real antes de escribirla; lo que el
registro no dice no se rellena con un valor plausible.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartera.firds import (apalancado, decode_cfi, domicile, es_etc,
                           etc_candidatos, fold, iter_listings, mejor_nombre,
                           sin_paraguas)

NS = "urn:iso:std:iso:20022:tech:xsd:auth.017.001.02"


def _reg(isin, name, cfi, ccy, mic, lei="5493001", first="2019-05-23T05:00:00Z"):
    return (f'<RefData xmlns="{NS}"><FinInstrmGnlAttrbts>'
            f"<Id>{isin}</Id><FullNm>{name}</FullNm><ClssfctnTp>{cfi}</ClssfctnTp>"
            f"<NtnlCcy>{ccy}</NtnlCcy></FinInstrmGnlAttrbts><Issr>{lei}</Issr>"
            f"<TradgVnRltdAttrbts><Id>{mic}</Id><FrstTradDt>{first}</FrstTradDt>"
            "</TradgVnRltdAttrbts></RefData>")


def _doc(*regs):
    return io.BytesIO((f'<?xml version="1.0" encoding="UTF-8"?><BizData xmlns="{NS}">'
                       + "".join(regs) + "</BizData>").encode())


# Un ETF que acumula, en tres plazas y con tres nombres distintos — que es
# exactamente lo que hace el fichero real.
#
# Es una FUNCIÓN y no una constante a propósito: `iterparse` consume el flujo, y
# un `BytesIO` compartido dejaría a cada test leyendo lo que el anterior ya se
# comió. Ese fallo se lee como «XML mal formado», que apunta a cualquier sitio
# menos al de verdad.
def muestra():
    return _doc(
        _reg("IE00B4L5Y983", "iShares III Plc - iShares Core MSCI World UCITS ETF",
             "CEOGES", "USD", "XETR"),
        _reg("IE00B4L5Y983", "iShsIII-Core MSCI World U.ETF Reg.Shs USD (Acc) o.N.",
             "CEOGES", "USD", "MTAA"),
        _reg("IE00B4L5Y983", "ISHSIII-CORE MSCI WLD DLA", "CEOGES", "USD", "XAMS"),
        _reg("IE00B3RBWM25", "Vanguard FTSE All-World UCITS ETF Distributing",
             "CEOIES", "USD", "XETR"),
        _reg("LU0055649056", "DWS Gold Plus", "CIOIES", "EUR", "XFRA"),
    )


# ── lo que el CFI permite afirmar ─────────────────────────────────────────
def test_el_cfi_dice_si_acumula_o_reparte():
    """Cuarta letra: `G` acumula, `I` reparte. Comprobado 5/5 contra ISIN
    conocidos (IWDA, VWCE, CSPX, EIMI acumulan; VWRL reparte)."""
    assert decode_cfi("CEOGES")["dist_policy"] == "acumulación"
    assert decode_cfi("CEOIES")["dist_policy"] == "distribución"
    assert decode_cfi("CEOJES")["dist_policy"] == "mixta"


def test_el_cfi_separa_ETF_de_fondo():
    assert decode_cfi("CEOGES")["kind"] == "ETF"
    assert decode_cfi("CIOIES")["kind"] == "Fondo"


def test_lo_que_el_registro_no_dice_se_queda_en_None():
    """`None` es «el registro no lo dice», y no es lo mismo que «no lo tiene».
    Un CFI corto o de otra categoría no se rellena con un valor plausible: sobre
    un dato inventado se construyen filtros que mienten."""
    assert decode_cfi("CE")["dist_policy"] is None
    assert decode_cfi("")["kind"] is None
    assert decode_cfi("ESVUFR")["is_civ"] is False       # una acción, no un fondo
    assert decode_cfi("ESVUFR")["kind"] is None


def test_el_domicilio_sale_del_ISIN():
    """Es lo más cerca de «¿es UCITS?» que llega este registro, y NO es lo mismo:
    FIRDS no publica el régimen regulatorio. Por eso se llama domicilio."""
    assert domicile("IE00B4L5Y983") == "IE"
    assert domicile("LU0055649056") == "LU"
    assert domicile("") is None


# ── elegir cómo se llama un fondo ─────────────────────────────────────────
def test_se_quita_la_sociedad_paraguas():
    """«iShares III Plc - » se repite en decenas de fondos que no tienen nada que
    ver entre sí: dejarlo delante empuja el nombre del producto fuera de la
    pantalla justo donde se lee."""
    assert sin_paraguas("iShares III Plc - iShares Core MSCI World UCITS ETF") \
        == "iShares Core MSCI World UCITS ETF"
    assert sin_paraguas("Vanguard Funds Plc - Vanguard FTSE All-World UCITS ETF") \
        == "Vanguard FTSE All-World UCITS ETF"


def test_un_guion_que_NO_es_una_sociedad_se_respeta():
    """«Amundi MDAX ESG II UCITS ETF - EUR (Dist)»: lo de detrás del guion es la
    clase, no el producto. Recortarlo aquí borraría lo que distingue una clase
    de otra."""
    n = "Amundi MDAX ESG II UCITS ETF - EUR (Dist)"
    assert sin_paraguas(n) == n


def test_el_nombre_que_se_ensena_es_el_comercial():
    """Ni el más largo (ése es el legal, empieza por la sociedad y acaba en el
    índice replicado) ni el más frecuente (ése es el abreviado alemán, que gana
    por número de plazas)."""
    assert mejor_nombre([
        "iShsIII-Core MSCI World U.ETF Registered Shs USD (Acc) o.N.",
        "ISHARES III PUBLIC LIMITED COMPANY - ISHARES CORE MSCI WORLD CMN CLASS ETF",
        "iShares Core MSCI World UCITS ETF",
        "ISHSIII-CORE MSCI WLD DLA",
    ]) == "iShares Core MSCI World UCITS ETF"


# ── doblar líneas en fondos ───────────────────────────────────────────────
def test_una_ISIN_con_tres_plazas_es_UN_fondo():
    """Se busca un fondo y después dónde comprarlo, no al revés. Devolver las
    plazas por separado repetiría la misma respuesta hasta 48 veces."""
    fondos, lineas = fold(iter_listings(muestra()))

    assert len(fondos) == 3
    assert fondos["IE00B4L5Y983"]["n_listings"] == 3
    assert sorted(x["mic"] for x in lineas if x["isin"] == "IE00B4L5Y983") \
        == ["MTAA", "XAMS", "XETR"]


def test_se_guardan_TODOS_los_nombres_para_buscar():
    """Quien escriba el nombre abreviado que trae su bróker tiene que encontrar
    lo mismo que quien escriba el comercial. Guardar sólo el elegido dejaría
    fuera a la mitad de la gente que ya sabe lo que busca."""
    fondos, _ = fold(iter_listings(muestra()))
    f = fondos["IE00B4L5Y983"]

    assert len(f["aliases"]) == 3
    assert any("iShsIII" in a for a in f["aliases"])
    assert f["name"] == "iShares Core MSCI World UCITS ETF"   # el que se enseña


def test_la_divisa_cuelga_del_fondo_no_de_la_plaza():
    """Comprobado sobre el fichero real: de los 18.376 ISIN, CERO tienen más de
    una divisa entre sus plazas. `NtnlCcy` es la moneda de DENOMINACIÓN. Ponerla
    en la línea insinuaría que el registro dice en qué divisa comprarías en
    Milán, y no lo dice."""
    fondos, lineas = fold(iter_listings(muestra()))

    assert fondos["IE00B4L5Y983"]["ccy"] == "USD"
    assert all("ccy" not in x for x in lineas)


def test_un_registro_sin_ISIN_no_inventa_un_fondo():
    fondos, lineas = fold(iter_listings(_doc(
        _reg("", "Sin identificar", "CEOGES", "EUR", "XETR"),
        _reg("IE00B4L5Y983", "iShares Core MSCI World UCITS ETF", "CEOGES", "USD", "XETR"))))

    assert list(fondos) == ["IE00B4L5Y983"]
    assert len(lineas) == 1


# ── ETC: deuda por fuera, producto cotizado por dentro ────────────────────
# Un ETC (oro físico, materias primas) es jurídicamente deuda y vive en
# `FULINS_D`. Ahí no hay CFI que los separe: el oro de Amundi es `DEMYRT`, los de
# WisdomTree `DAZSPR`, los de Xtrackers `DAZSFR`, y los grupos `DA`/`DE` tienen
# más de un millón de líneas de bonos corrientes.
def _deuda(*regs):
    return _doc(*regs)


def test_un_ETC_se_reconoce_por_grupo_CFI_Y_nombre():
    """Ninguna de las dos señales sola vale: el grupo trae bonos a millones y el
    nombre suelto trae los bonos de EATON, cuyo ticker es ETN."""
    assert es_etc("DEMYRT", ["Amundi Physical Gold ETC"])          # el oro real
    assert es_etc("DAZSPR", ["WisdomTree Physical Gold ETCS"])
    assert not es_etc("DEMYRT", ["Bono corriente 2031"])           # grupo sí, nombre no
    assert not es_etc("DBFGGR", ["ETN  3.950  3/6/29 c29 EATON CORP"])   # nombre sí, grupo no


def test_el_bono_de_EATON_no_es_un_ETC():
    """Medido: de los 988 ISIN de deuda con ETC/ETP/ETN en el nombre, 79 se caen
    al exigir grupo `DA`/`DE`, y los 79 son bonos de EATON Corp — «ETN» es su
    ticker. Sin el grupo, el buscador de fondos ofrecería deuda corporativa."""
    assert not es_etc("DBFGGR", ["ETN  4.500  3/6/33 c33 EATON CORP"])
    assert not es_etc("DBFNAR", ["ETN  3.625  5/9/35 c35 EATON CAPITAL UNLIMITED CO"])


def test_basta_con_que_UNA_plaza_lo_declare():
    """El 41% de los ETC sólo dicen «ETC» en algunas de sus plazas. Mirar un solo
    nombre perdería cuatro de cada diez."""
    assert es_etc("DAZSPR", ["Gold Bullion Securities Ltd.", "Gold Bullion Sec. ETCS"])


def test_se_avisa_del_apalancamiento():
    """Son dos tercios del universo de ETC y se llaman casi igual que el producto
    físico. No se esconden —quien tenga uno tiene que poder apuntarlo— pero un
    «Gold 3x Daily Short» sin etiquetar, junto al oro físico, es una trampa."""
    assert apalancado(["Gestora Delta Gold 3x Daily Short"])
    assert apalancado(["Leverage Shares PLC ETP -3x Short Silver Miners"])
    assert apalancado(["GraniteShares 3x Long BP Daily ETNS"])
    assert not apalancado(["Amundi Physical Gold ETC"])
    assert not apalancado(["WisdomTree Physical Swiss Gold"])


def test_el_ETC_entra_en_el_catalogo_con_su_tipo():
    fondos, _ = fold(iter_listings(_deuda(
        _reg("FR0013416716", "Amundi Physical Gold ETC", "DEMYRT", "EUR", "XPAR"),
        _reg("FR0013416716", "Amundi Physical Metals PLC ETC Physic", "DEMYRT", "EUR", "XETR"),
        _reg("IE00BZ3XGLD1", "Gestora Delta Gold 3x Daily Leveraged ETP", "DAZSGR", "USD", "XLON"),
    )))

    assert fondos["FR0013416716"]["kind"] == "ETC"
    assert fondos["FR0013416716"]["leveraged"] is False
    assert fondos["IE00BZ3XGLD1"]["leveraged"] is True


def test_un_bono_corriente_se_queda_SIN_tipo():
    """Sin tipo el ingestor lo tira. Es la única defensa entre un buscador de
    fondos y los 642.598 ISIN de deuda del registro."""
    fondos, _ = fold(iter_listings(_deuda(
        _reg("US278058DV11", "Eaton Corp. DL-Notes 2026(26/28)", "DBFGGR", "USD", "XNYS"))))

    assert fondos["US278058DV11"]["kind"] is None


def test_la_primera_pasada_solo_devuelve_los_candidatos():
    """Existe por MEMORIA: doblar los 642.598 ISIN de deuda para quedarse con 909
    picaba en 1,47 GB, por encima del tope de la unidad de systemd. Con esta
    pasada previa el pico medido es de 201 MB."""
    cand = etc_candidatos(iter_listings(_deuda(
        _reg("FR0013416716", "Amundi Physical Gold ETC", "DEMYRT", "EUR", "XPAR"),
        _reg("US278058DV11", "Eaton Corp. DL-Notes 2026(26/28)", "DBFGGR", "USD", "XNYS"),
        _reg("US278058DW93", "ETN  3.950  3/6/29 c29 EATON CORP", "DBFGGR", "USD", "XNYS"))))

    assert cand == {"FR0013416716"}


def test_el_filtro_se_aplica_ANTES_de_acumular():
    """`keep` no es un lujo: sin él, el fichero de deuda entra entero en memoria
    para acabar tirando el 99,86%."""
    fondos, lineas = fold(
        iter_listings(_deuda(
            _reg("FR0013416716", "Amundi Physical Gold ETC", "DEMYRT", "EUR", "XPAR"),
            _reg("US278058DV11", "Eaton Corp. DL-Notes", "DBFGGR", "USD", "XNYS"))),
        keep=lambda r: r["isin"] == "FR0013416716")

    assert list(fondos) == ["FR0013416716"]
    assert len(lineas) == 1


def test_el_nombre_elegido_no_depende_del_azar():
    """`max` sobre un conjunto devuelve el primer máximo, y ese orden cambia con
    la aleatorización del hash entre procesos. Con 225 ISIN empatados en el
    fichero real, 225 fondos podían cambiar de nombre cada semana sin que
    cambiara un dato. El empate se rompe por orden alfabético."""
    empatados = ["Amundi Index MSCI World AK (C)", "Amundi Index MSCI World AE (C)"]

    assert mejor_nombre(empatados) == "Amundi Index MSCI World AE (C)"
    assert mejor_nombre(list(reversed(empatados))) == "Amundi Index MSCI World AE (C)"
    assert mejor_nombre({*empatados}) == "Amundi Index MSCI World AE (C)"
