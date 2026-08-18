"""Texto -> dato. Todo lo que hay que entender de lo que escribe una persona o
exporta un banco, antes de que sea un número con el que contar dinero.

Puro por definición: ni red, ni disco, ni reloj. Cada función de aquí ha tenido
al menos un defecto que producía un NÚMERO EQUIVOCADO en vez de un error —
`_norm_side` llegó a leer "Suscripción" como una venta— y ese es justo el tipo
de fallo que no se ve hasta que ya está en la pantalla de alguien.

Los nombres van sin guion bajo: dejan de ser detalles privados de `dashboard.py`
para ser la interfaz de un módulo. `dashboard.py` los reexporta con su nombre
antiguo, porque ahí dentro siguen siendo internos.
"""
from __future__ import annotations

import csv
import math
import re

import pandas as pd

# Sort key for undated movements. Un movimiento sin fecha tiene que ordenarse el
# ÚLTIMO, no el primero: la cadena vacía va antes que cualquier fecha real, y
# eso convertía un movimiento sin fecha en el más antiguo de la posición, lo que
# reseteaba en silencio su precio medio.
FAR_FUTURE = "9999-12-31"

_ACC = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")

# Sinónimos de columna que reconoce el importador, y a la vez la cabecera exacta
# que escribe el exportador: por eso un fichero exportado se vuelve a importar.
COLSYN = {
    "date": {"fecha", "date", "dia", "fecha operacion", "f valor", "fecha valor", "f. valor"},
    "ticker": {"ticker", "simbolo", "symbol", "activo", "valor", "isin", "instrumento", "producto"},
    "side": {"tipo", "side", "operacion", "movimiento", "compra/venta", "b/s", "c/v", "accion", "sentido"},
    "quantity": {"cantidad", "qty", "quantity", "shares", "titulos", "acciones", "unidades", "numero",
                 "participaciones", "num", "nominal", "n titulos", "no titulos"},
    "price": {"precio", "price", "precio unitario", "cotizacion", "valor liquidativo", "precio compra", "precio medio"},
    "fee": {"comision", "fee", "fees", "gastos", "coste", "costes", "comisiones", "corretaje"},
    "name": {"nombre", "name", "denominacion", "fondo", "descripcion del activo", "nombre del activo"},
    "note": {"nota", "note", "comentario", "observaciones"},
}

CARTERA_EXPORT_COLS = ["fecha", "ticker", "nombre", "tipo", "cantidad", "precio",
                       "comision", "nota"]

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

_CORP_SUFFIX = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY",
                "LTD", "LIMITED", "PLC", "SA", "NV", "AG", "SE", "LLC", "LP",
                "HOLDINGS", "HOLDING", "GROUP", "THE",
                "USD"}   # Yahoo tags crypto names with a quote-ccy suffix: "Ethereum USD"


# ── números, columnas, fechas ─────────────────────────────────────────────
def num(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return None if (isinstance(x, float) and math.isnan(x)) else float(x)
    s = str(x).strip().replace("€", "").replace("$", "").replace(" ", "").replace("%", "")
    if not s or s.lower() == "nan":
        return None
    if "," in s and "." in s:                 # 1.234,56 -> 1234.56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def csv_num(x) -> str:
    """Un número guardado como la cadena más corta que se relee como el MISMO
    float.

    `repr` hace ese viaje de ida y vuelta exacto en Python 3; un formato de
    ancho fijo no. Una cantidad redondeada al salir es un precio medio
    equivocado al volver a entrar, que es la clase cara de error: parece un
    número correcto.
    """
    if x is None:
        return ""
    try:
        return repr(float(x))
    except (TypeError, ValueError):
        return ""


def norm_col(c):
    return str(c).strip().lower().translate(_ACC)


def norm_date(x):
    if x is None:
        return ""
    s = str(x).strip()
    if not s or s.lower() == "nan":
        return ""
    # Respect ISO YYYY-MM-DD as-is; only use dayfirst for European DD/MM/YYYY forms
    iso = len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-"
    try:
        return pd.to_datetime(s[:10] if iso else s, dayfirst=not iso).strftime("%Y-%m-%d")
    except Exception:
        return s[:10]


# ── compra o venta ────────────────────────────────────────────────────────
# Vocabulario de banco y de fondo, sin acentos y en minúscula. Se compara por
# PALABRA ENTERA, no por prefijo: `startswith("s")` convertía "Suscripción" —que
# es una COMPRA de participaciones— en una venta, y "Reembolso" en una compra.
# Las dos son las palabras estándar de un extracto de fondos español, así que
# las dos filas más comunes de una exportación se apuntaban del revés.
_SIDE_WORDS = {
    "buy": {"compra", "compras", "comprar", "buy", "bought", "purchase", "adquisicion",
            "suscripcion", "suscripciones", "aportacion", "aportaciones", "entrada",
            "alta", "ingreso", "cargo", "inversion"},
    "sell": {"venta", "ventas", "vender", "sell", "sold", "sale", "reembolso",
             "reembolsos", "rescate", "amortizacion", "retirada", "salida", "baja",
             "disposicion", "abono"},
    # La renta que paga un instrumento sin que se venda nada. Gana a las otras
    # dos (ver el orden del bucle) porque un extracto español la escribe como
    # "Abono dividendo" o "Pago de cupon": las dos palabras están en la misma
    # celda, y la que manda es la que dice QUÉ es, no la que dice por dónde
    # entró el dinero. Sin esa prioridad, un dividendo se apuntaba como venta y
    # se comía participaciones que nadie había vendido.
    # `div` va en la lista porque es el código CANÓNICO que guarda la base y que
    # manda la API. Un valor que el sistema escribe y luego no sabe releer es un
    # viaje de ida y vuelta roto: el dividendo volvía a entrar como compra.
    "div": {"div", "dividendo", "dividendos", "dividend", "dividends", "cupon",
            "cupones", "coupon", "reparto", "distribucion", "interes",
            "intereses", "rendimiento", "rendimientos"},
}
# Los códigos de una letra y los signos sólo valen cuando son el valor ENTERO.
# Como subcadena están en todas partes —un guion dentro de una fecha, la "s" de
# cualquier palabra— y cada acierto falso invierte una operación.
_SIDE_CODES = {"c": "buy", "b": "buy", "+": "buy",
               "v": "sell", "s": "sell", "-": "sell",
               "d": "div"}


def norm_side(v, qty=None):
    """Dirección del movimiento: código exacto, palabras enteras, signo, y compra.

    Devuelve "buy", "sell" o "div". El vocabulario desconocido cae al SIGNO de
    la cantidad y no a una suposición a partir de la primera letra: equivocarse
    aquí invierte una operación en silencio.

    Un dividendo NUNCA sale del signo ni de una suposición: hay que nombrarlo.
    Es el único movimiento que no mueve la posición, así que confundirlo con una
    compraventa por accidente cambia la cantidad de títulos, y eso no se ve
    hasta que el precio medio ya está mal.
    """
    s = norm_col(v)                                    # lowercase + strip accents
    sign = "sell" if (qty is not None and qty < 0) else "buy"
    if not s or s == "nan":
        return sign
    if s in _SIDE_CODES:
        return _SIDE_CODES[s]
    words = set(re.findall(r"[a-z]+", s))
    for side in ("div", "sell", "buy"):                # renta > venta explícita > compra
        if words & _SIDE_WORDS[side]:
            return side
    return sign


# El texto que se escribe en el CSV y el que se lee en pantalla. Palabra
# completa, nunca un código de una letra: una "d" suelta en una hoja de cálculo
# está a un dedazo de ser otra cosa.
SIDE_ES = {"buy": "compra", "sell": "venta", "div": "dividendo"}


def side_es(side: str) -> str:
    return SIDE_ES.get(side, "compra")


# ── identidad de un instrumento ───────────────────────────────────────────
def looks_like_isin(s: str) -> bool:
    return bool(_ISIN_RE.match(str(s).strip().upper()))


def symbol_isin(symbol: str) -> str:
    """El ISIN del que nace un símbolo de Yahoo, o "" — `IE000M7V94E1.SG` -> `IE000M7V94E1`."""
    root = str(symbol or "").strip().upper().split(".")[0]
    return root if looks_like_isin(root) else ""


# Yahoo's `quoteType` is not a dependable instrument classifier for European
# listings, and the portfolio proves it: IE000M7V94E1.SG came back ETF when it
# was imported and MUTUALFUND when the same holding was added by hand weeks
# later, so one position ended up wearing two different badges. Both answers
# cannot be right, and the second is the impossible one — `.SG` is the Stuttgart
# LINE of an instrument, and an unlisted fund has no line on any exchange.
# FR0013416716.SG (an ETC) is reported MUTUALFUND too.
#
# The symbol itself says what the label was supposed to, and it does not change
# between requests:
#   * `0P...`         Yahoo's synthetic symbol for a fund quoted at NAV, unlisted
#   * `<ISIN>.<MIC>`  a line on an exchange — whatever it is, it is not unlisted
# Only that contradiction is overruled. A type we were never told stays unknown
# (guessing "ETF" for a bond ETC would just be a nicer-looking wrong answer),
# and a label the user wrote — ETC — is never overwritten.
def instrument_kind(symbol: str, reported: str = "") -> str:
    """Instrument type for a Yahoo symbol: the symbol decides listed vs unlisted,
    the reported type fills in the rest. "" when nothing is known."""
    sym = str(symbol or "").strip().upper()
    kind = str(reported or "").strip()
    if sym.startswith("0P"):                       # unlisted fund, quoted at NAV
        return "Fondo"
    if "." in sym and symbol_isin(sym):            # a listing: it is not unlisted
        return "ETF" if kind == "Fondo" else kind
    return kind


def clean_company_name(raw: str) -> str:
    """"Tesla, Inc." -> "TESLA", "Amazon.com, Inc." -> "AMAZON". Cae al nombre en
    mayúsculas cuando limpiarlo lo dejaría vacío (p. ej. el nombre de un fondo)."""
    n = re.sub(r"\.com\b", "", str(raw or "").strip(), flags=re.IGNORECASE)
    tokens = [t for t in re.split(r"[\s,]+", n) if t]
    while tokens and tokens[-1].strip(".").upper() in _CORP_SUFFIX:
        tokens.pop()
    cleaned = " ".join(tokens).strip(" ,.-")
    return (cleaned or n).strip().upper()


def name_from_meta(meta: object) -> str:
    """Nombre limpio a partir del `meta` que YA trajo `fetch_daily`, o "".

    Antes esto abría su propia conexión al mismo endpoint del que ya venía el
    histórico: 12 s de timeout encadenados DETRÁS de los 30 s del precio, o sea
    42 s en el peor caso contra un abort de cliente de 25 s. Y como un fallo no
    se cacheaba, una degradación parcial de Yahoo hacía repagar esos 12 s en
    cada refresco. Leyendo el dato que ya está descargado no hay ni petición
    extra, ni timeout que encadenar, ni caché que mantener.
    """
    if not isinstance(meta, dict):
        return ""
    return clean_company_name(meta.get("longName") or meta.get("shortName") or "")


# ── ficheros ──────────────────────────────────────────────────────────────
def sniff_sep(data: bytes) -> str:
    """Delimitador, mirando sólo la línea de cabecera.

    `sep=None` obliga a pandas a usar su motor de Python, ~7x más lento. La
    cabecera es toda la evidencia que hace falta, y una suposición equivocada se
    ve al momento (no se detecta ninguna columna) en vez de corromper valores en
    silencio.
    """
    try:
        head = data[:8192].decode("utf-8", "replace").splitlines()[0]
        return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except Exception:
        return ","


def mov_key(r):
    """Identidad de un movimiento, para detectar duplicados."""
    return (str(r.get("date") or ""), str(r.get("ticker") or "").upper(),
            str(r.get("side") or ""), round(float(r.get("quantity") or 0), 8),
            round(float(r.get("price") or 0), 8))
