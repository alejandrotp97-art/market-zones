"""FIRDS: el registro de instrumentos de la UE, leído sin saber de dónde viene.

FIRDS (*Financial Instruments Reference Data System*) es lo que ESMA publica
cada semana con todo lo admitido a negociación en la Unión. Este módulo sólo
sabe convertir esos registros en datos; quién los descarga y dónde se guardan es
problema de `ingest_firds.py`, no de aquí — la regla del paquete.

Lo que trae cada registro es una LÍNEA de cotización, no un fondo: el mismo ETF
aparece una vez por cada plaza donde se negocia (mediana 7, máximo 48 medidos
sobre el fichero del 2026-08-22). Doblarlas en fondos es el trabajo de `fold`.

Lo que NO trae, y por lo tanto este módulo no inventa: gastos corrientes (TER),
método de réplica, patrimonio, índice replicado, ni una bandera de UCITS.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# El esquema ISO 20022 con el que ESMA publica los ficheros FULINS.
NS = "{urn:iso:std:iso:20022:tech:xsd:auth.017.001.02}"

# ── CFI (ISO 10962), categoría C = vehículos de inversión colectiva ─────────
# Segunda letra = el grupo. Las tres primeras están COMPROBADAS contra
# instrumentos reales del fichero; el resto salen de la norma y se traducen
# igual, pero nunca se usan para filtrar en pantalla.
CIV_GROUP = {
    "E": "ETF",             # comprobado: iShares Core MSCI World, Vanguard All-World
    "I": "Fondo",           # comprobado: fondos cotizados
    "B": "REIT",            # comprobado: Yuexiu Real Estate Investment Trust
    "F": "Fondo de fondos",
    "H": "Hedge fund",
    "M": "Otros fondos",
    "P": "Capital riesgo",
    "S": "Fondo de pensiones",
}

# Cuarta letra = política de reparto. COMPROBADO 5 de 5 contra instrumentos
# conocidos: IWDA, VWCE, CSPX y EIMI dan `G` y acumulan; VWRL da `I` y reparte.
# Es el único atributo económico que el registro regala, y por eso se usa.
DIST_POLICY = {"I": "distribución", "G": "acumulación", "J": "mixta"}


def decode_cfi(cfi: str) -> dict:
    """CFI de seis letras -> lo que de verdad se puede afirmar de él.

    Devuelve siempre las tres claves. `None` significa «el registro no lo dice»,
    que es distinto de «no lo tiene»: un CFI corto o desconocido no se rellena
    con un valor plausible.
    """
    c = (cfi or "").strip().upper()
    es_civ = c[:1] == "C"
    return {
        "kind": CIV_GROUP.get(c[1:2]) if es_civ and len(c) > 1 else None,
        "dist_policy": DIST_POLICY.get(c[3:4]) if es_civ and len(c) > 3 else None,
        "is_civ": es_civ,
    }


def domicile(isin: str) -> str | None:
    """Los dos primeros caracteres de un ISIN son el país de emisión.

    Es lo más cerca que se puede estar de «¿es UCITS?» con este registro en la
    mano, y no es lo mismo: FIRDS no publica el régimen regulatorio. Se llama
    domicilio porque es un domicilio. Filtrar por la palabra «UCITS» del nombre
    sería peor: sólo la llevan el 27,9% de los ETF del fichero.

    Salvedad que la pantalla tiene que respetar: `XS` no es un país, es el
    prefijo de las emisiones internacionales liquidadas en Euroclear/Clearstream,
    y lo llevan bastantes ETC. Aquí se devuelve tal cual —es lo que dice el
    ISIN— y quien lo enseñe no puede llamarlo domicilio.
    """
    i = (isin or "").strip().upper()
    return i[:2] if len(i) >= 2 and i[:2].isalpha() else None


# ── ETC y ETP: productos cotizados que NO son fondos ───────────────────────
# Un ETC (oro físico, materias primas) es jurídicamente DEUDA, no un vehículo de
# inversión colectiva, así que vive en `FULINS_D` y no en `_C`. Y ahí no hay un
# CFI que los separe: medido sobre el fichero del 2026-08-22, el oro de Amundi es
# `DEMYRT`, los de WisdomTree `DAZSPR`, los de Xtrackers `DAZSFR` — y los grupos
# `DE` y `DA` tienen entre los dos MÁS DE UN MILLÓN de líneas de bonos normales.
#
# Así que aquí el nombre SÍ se usa como filtro, al revés que con los UCITS. La
# diferencia no es de criterio, es de qué hay: para un ETF existía una señal
# estructural completa (CFI `CE`) y la palabra «UCITS» sólo habría estorbado;
# para un ETC no existe ninguna, y el emisor sí escribe ETC/ETP/ETN en el nombre.
#
# Medido: de 642.598 ISIN de deuda, 988 lo dicen en alguna variante del nombre y
# 909 están además en `DA`/`DE`. Los 79 que se caen son bonos de **EATON** Corp,
# donde «ETN» es su ticker y no un producto cotizado. Recall comprobado 7/7
# contra ETC reales conocidos.
_ETC_NOMBRE = re.compile(r"\b(ETC|ETCS|ETP|ETPS|ETN|ETNS)\b", re.IGNORECASE)
_ETC_GRUPOS = ("DA", "DE")     # respaldados por activo · estructurados sin protección

# El 41% de los ETC sólo lo dicen en ALGUNA de sus plazas: mirar un único nombre
# perdería cuatro de cada diez.
def es_etc(cfi: str, names) -> bool:
    c = (cfi or "").strip().upper()
    return c[:2] in _ETC_GRUPOS and any(_ETC_NOMBRE.search(n or "") for n in names)


# «3x», «-3x Short», «Daily Leveraged». Son el 66% de los ETC del registro, y
# junto al oro físico en la misma lista, sin distinguir, son una trampa: se
# parecen en el nombre y no se parecen en nada más. No se esconden —si tienes uno
# tienes que poder apuntarlo— pero se DICEN.
#
# El apalancamiento se lee del nombre porque el emisor está obligado a ponerlo
# ahí; el registro no lo publica en ningún campo.
_APALANCADO = re.compile(
    r"(\b[-+]?\d+(?:[.,]\d+)?\s*x\b|\bshort\b|\bleverage|\bapalanc|\bbear\b|\bultra\b)",
    re.IGNORECASE)


def apalancado(names) -> bool:
    return any(_APALANCADO.search(n or "") for n in names)


def etc_candidatos(listings) -> set:
    """Primera pasada por el fichero de deuda: sólo los ISIN que pueden ser ETC.

    Existe por MEMORIA, y el número lo dice todo: doblar los 642.598 ISIN de
    deuda para quedarse con 909 costaba **1,47 GB** de pico, por encima del tope
    de la unidad de systemd. Aquí sólo se guarda un conjunto de cadenas.

    Basta con que UNA plaza lo declare —el 41% no lo dicen en todas—, y como esta
    pasada ve todas las líneas, el «alguna variante» se conserva entero.
    """
    return {r["isin"] for r in listings if es_etc(r["cfi"], (r["name"],))}


# ── Elegir CÓMO se llama un fondo ──────────────────────────────────────────
# Cada ISIN llega con diez o quince nombres distintos: el comercial, el legal
# del paraguas, el alemán abreviado y varios mnemónicos. Medido sobre el fichero
# del 2026-08-22: 13 variantes para iShares Core MSCI World, 15 para el Core
# S&P 500. Ninguna viene marcada como «la buena».
#
# Todas se guardan para BUSCAR —quien escriba «iShs» tiene que encontrarlo igual
# que quien escriba «iShares»—, y de todas se elige una para ENSEÑAR.

# «iShares III Plc - iShares Core MSCI World UCITS ETF»: lo de delante del guion
# es la sociedad paraguas, no el producto, y se repite en decenas de fondos que
# no tienen nada que ver entre sí.
_PARAGUAS = re.compile(
    r"^.{3,60}?\s(?:P\.?L\.?C\.?|PUBLIC LIMITED COMPANY|SICAV|FCP|N\.?V\.?|S\.?A\.?)\s+-\s+",
    re.IGNORECASE)
# Un punto pegado a la siguiente letra delata la abreviatura de mercado alemán:
# «U.ETF», «Reg.Shs», «o.N.». Son nombres correctos, pero nadie los escribe.
_ABREVIADO = re.compile(r"\.\S")


def sin_paraguas(name: str) -> str:
    """Quita el nombre de la sociedad gestora cuando precede al del producto."""
    n = (name or "").strip()
    return _PARAGUAS.sub("", n).strip() or n


def _legibilidad(name: str) -> tuple:
    """Orden de preferencia para ENSEÑAR un nombre. Mayor es mejor."""
    return (0 if _ABREVIADO.search(name) else 1,   # sin abreviaturas de punto
            0 if name.isupper() else 1,            # las mayúsculas son el nombre legal
            len(name))                             # a igualdad, el más informativo


def mejor_nombre(names) -> str:
    """El nombre comercial entre todas las variantes de una misma ISIN.

    No es «el más largo»: el más largo suele ser el legal, que empieza por la
    sociedad paraguas y termina en el índice replicado. Tampoco «el más
    frecuente»: ése es el abreviado alemán, que gana por número de plazas.
    """
    limpios = {sin_paraguas(n) for n in names if n}
    if not limpios:
        return ""
    # `sorted` antes del `max` NO es adorno. `max` devuelve el primer máximo que
    # encuentra, y sobre un conjunto ese orden depende de la aleatorización del
    # hash de cadenas, que cambia en cada proceso. Medido sobre el fichero real:
    # 225 de los 18.376 ISIN tienen dos nombres EMPATADOS en esta clave, así que
    # sin ordenar primero, 225 fondos podían cambiar de nombre en pantalla en
    # cada ingesta semanal sin que cambiara un solo dato del registro.
    return max(sorted(limpios), key=_legibilidad)


def iter_listings(stream):
    """XML de FIRDS -> una línea de cotización por registro.

    `iterparse` con `clear()` en cada nodo: el fichero de fondos son 91 MB
    descomprimidos y cargarlo entero costaría cien veces lo que ocupa el
    resultado. Se lee en flujo y se suelta lo ya visto.
    """
    for _, el in ET.iterparse(stream, events=("end",)):
        if not el.tag.endswith("}RefData"):
            continue
        gen = el.find(f"{NS}FinInstrmGnlAttrbts")
        if gen is None:
            el.clear()
            continue
        isin = (gen.findtext(f"{NS}Id") or "").strip().upper()
        if isin:
            venue = el.find(f"{NS}TradgVnRltdAttrbts")
            yield {
                "isin": isin,
                "name": (gen.findtext(f"{NS}FullNm") or "").strip(),
                "cfi": (gen.findtext(f"{NS}ClssfctnTp") or "").strip().upper(),
                "ccy": (gen.findtext(f"{NS}NtnlCcy") or "").strip().upper() or None,
                "issuer_lei": (el.findtext(f"{NS}Issr") or "").strip() or None,
                "mic": (venue.findtext(f"{NS}Id") or "").strip().upper() or None
                       if venue is not None else None,
                "first_trade": ((venue.findtext(f"{NS}FrstTradDt") or "")[:10] or None)
                               if venue is not None else None,
            }
        el.clear()


def fold(listings, keep=None) -> tuple[dict, list]:
    """Líneas sueltas -> (fondos por ISIN, líneas). Es la forma en que se busca.

    Se busca un FONDO y después se elige dónde comprarlo, no al revés. Un
    buscador que devolviera las 48 líneas de un ETF como 48 resultados no estaría
    ayudando a decidir: estaría repitiendo la misma respuesta.

    `keep` filtra POR LÍNEA, antes de acumular nada. No es un lujo: sin él, el
    fichero de deuda mete 642.598 ISIN en memoria para acabar quedándose con 909.

    Cada fondo se queda con `name` (el comercial, para enseñar) y `aliases` (todas
    las variantes, para buscar). Guardar sólo el elegido dejaría sin encontrar a
    quien escriba el nombre abreviado que trae su bróker.

    **La divisa cuelga del FONDO, no de la línea, y eso está comprobado**: de los
    18.376 ISIN del fichero, CERO tienen más de una divisa entre sus plazas. El
    `NtnlCcy` de FIRDS es la moneda en que está DENOMINADO el fondo, no en la que
    se cruza en cada mercado. Colgarla de la línea insinuaría que el registro
    dice en qué divisa comprarías en Milán, y no lo dice — eso sólo se sabe
    preguntando el precio.
    """
    fondos: dict[str, dict] = {}
    lineas = []
    for r in listings:
        if keep is not None and not keep(r):
            continue
        isin = r["isin"]
        f = fondos.get(isin)
        if f is None:
            cfi = decode_cfi(r["cfi"])
            f = fondos[isin] = {
                "isin": isin, "cfi": r["cfi"], "kind": cfi["kind"],
                "dist_policy": cfi["dist_policy"], "domicile": domicile(isin),
                "ccy": r["ccy"], "issuer_lei": r["issuer_lei"],
                "aliases": set(), "n_listings": 0,
            }
        if r["name"]:
            f["aliases"].add(r["name"])
        if f["issuer_lei"] is None and r["issuer_lei"]:
            f["issuer_lei"] = r["issuer_lei"]
        if r["mic"]:
            f["n_listings"] += 1
            lineas.append({"isin": isin, "mic": r["mic"],
                           "first_trade": r["first_trade"]})
    for f in fondos.values():
        f["aliases"] = sorted(f["aliases"])
        f["name"] = mejor_nombre(f["aliases"])
        # La clasificación de ETC va AQUÍ y no arriba porque necesita todos los
        # nombres a la vez: el 41% sólo se declaran ETC en alguna de sus plazas.
        if f["kind"] is None and es_etc(f["cfi"], f["aliases"]):
            f["kind"] = "ETC"
        f["leveraged"] = f["kind"] == "ETC" and apalancado(f["aliases"])
    return fondos, lineas
