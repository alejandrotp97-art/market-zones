"""Exposición agregada por divisa. Dos lecturas, y no dicen lo mismo.

La primera es exacta y poco informativa; la segunda es aproximada y es la que
importa. Enseñar sólo una de las dos engaña de una forma u otra:

  **Divisa de COTIZACIÓN** — en qué moneda se compra y se vende cada posición.
  Sale del libro, es exacta, y no dice casi nada sobre el riesgo real: un fondo
  indexado mundial cotizado en euros aparece aquí al 100% en euros, y dentro
  lleva dos tercios de dólares. Sirve para una sola cosa: saber en qué moneda
  te van a cobrar y a pagar.

  **Divisa ECONÓMICA** — dónde está el negocio que hay debajo. Se deriva de la
  exposición por países que ya calcula el módulo de geografía, abriendo cada
  fondo. Es la que responde «¿cuánto de mi patrimonio depende del dólar?».

Es exactamente la misma distinción que el mapa hace entre el domicilio legal de
un fondo y los países en los que invierte, y por el mismo motivo: colorear
Irlanda al 97% porque los fondos están domiciliados allí no dice nada cierto.

LO QUE ESTA APROXIMACIÓN NO CAPTURA
------------------------------------
Se asigna la divisa del PAÍS del negocio. Una empresa alemana que factura la
mitad en dólares aparece entera en euros, y una minera australiana que vende
materias primas cotizadas en dólares aparece entera en dólares. Afinar eso
exigiría la cuenta de resultados de cada compañía; queda dicho en vez de
insinuar una precisión que no hay.
"""
from __future__ import annotations

# La zona euro, enumerada y no adivinada: 20 estados. Un país europeo que NO
# esté aquí (Suiza, Reino Unido, Suecia, Polonia...) tiene su propia moneda, y
# meterlo en el saco del euro por cercanía geográfica sería justo el error que
# este módulo existe para no cometer.
EUROZONA = {"AT", "BE", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE", "IT",
            "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES"}

# País -> moneda. Tabla explícita y auditable: lo que no está aquí sale como
# «sin mapear» y se declara, en vez de caer en un cajón «otras» que se lee como
# si fuera poca cosa.
MONEDA_PAIS = {
    "US": "USD", "GB": "GBP", "JP": "JPY", "CH": "CHF", "CA": "CAD",
    "AU": "AUD", "NZ": "NZD", "CN": "CNY", "HK": "HKD", "TW": "TWD",
    "KR": "KRW", "IN": "INR", "SG": "SGD", "BR": "BRL", "MX": "MXN",
    "CL": "CLP", "CO": "COP", "PE": "PEN", "AR": "ARS", "SE": "SEK",
    "NO": "NOK", "DK": "DKK", "PL": "PLN", "CZ": "CZK", "HU": "HUF",
    "RO": "RON", "BG": "BGN", "TR": "TRY", "IL": "ILS", "ZA": "ZAR",
    "AE": "AED", "SA": "SAR", "QA": "QAR", "KW": "KWD", "EG": "EGP",
    "NG": "NGN", "MA": "MAD", "TH": "THB", "ID": "IDR", "MY": "MYR",
    "PH": "PHP", "VN": "VND", "PK": "PKR", "BD": "BDT", "RU": "RUB",
    "IS": "ISK", "UA": "UAH", "KZ": "KZT",
}


def moneda_de(iso2):
    """Moneda de un país, o "" si no está en la tabla."""
    c = str(iso2 or "").strip().upper()
    if not c:
        return ""
    if c in EUROZONA:
        return "EUR"
    return MONEDA_PAIS.get(c, "")


def by_quote_currency(positions, base="EUR"):
    """Reparto por divisa de COTIZACIÓN. Exacto, del libro.

    Sólo entran las posiciones valoradas: incluir una sin valor la contaría
    como cero y bajaría el peso de su divisa sin que se note.
    """
    acc, total, sin_valorar = {}, 0.0, 0
    for p in positions:
        if p.get("qty", 0) <= 1e-9:
            continue
        if not p.get("valued") or p.get("market_value") is None:
            sin_valorar += 1
            continue
        cu = (p.get("ccy") or "?").strip() or "?"
        v = float(p["market_value"])
        acc[cu] = acc.get(cu, 0.0) + v
        total += v
    filas = [{"ccy": k, "eur": round(v, 2),
              "pct": round(v / total * 100, 2) if total > 1e-9 else None}
             for k, v in acc.items()]
    filas.sort(key=lambda r: -r["eur"])
    return {"rows": filas, "total": round(total, 2), "unvalued": sin_valorar,
            "base": base}


def by_economic_currency(countries, mapped_eur=None, base="EUR"):
    """Reparto por divisa ECONÓMICA, desde la exposición por países.

    `countries` son las filas del módulo de geografía: `{"iso2", "eur"}`.
    `mapped_eur` es el patrimonio que esa transparencia sí pudo repartir; se
    usa para declarar sobre qué parte se está hablando.

    Un país fuera de la tabla NO se reparte entre los demás ni se mete en un
    saco «otras»: sale como `unmapped`, con su importe. Repartirlo inflaría en
    proporción todas las divisas conocidas y haría parecer completa una foto
    que no lo está.
    """
    acc, mapeado, sin_mapear, paises_sin = {}, 0.0, 0.0, []
    for c in countries or []:
        eur = float(c.get("eur") or 0.0)
        if eur <= 0:
            continue
        m = moneda_de(c.get("iso2"))
        if not m:
            sin_mapear += eur
            paises_sin.append(c.get("iso2") or "?")
            continue
        acc[m] = acc.get(m, 0.0) + eur
        mapeado += eur
    filas = [{"ccy": k, "eur": round(v, 2),
              "pct": round(v / mapeado * 100, 2) if mapeado > 1e-9 else None}
             for k, v in acc.items()]
    filas.sort(key=lambda r: -r["eur"])
    return {"rows": filas, "mapped": round(mapeado, 2),
            "unmapped": round(sin_mapear, 2),
            "unmapped_countries": sorted(set(paises_sin)),
            # Qué parte del patrimonio ha llegado hasta aquí. Sin esto, un
            # reparto sobre el 40% de la cartera se lee como el reparto de la
            # cartera entera.
            "coverage_pct": (round(mapeado / mapped_eur * 100, 1)
                             if (mapped_eur and mapped_eur > 1e-9) else None),
            "base": base}
