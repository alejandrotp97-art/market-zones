"""De movimientos a posiciones valoradas. La aritmética del dinero.

Este módulo no sabe de dónde salen los precios. Todo lo que necesita del mundo
exterior —divisa de un instrumento, tipo de cambio de hoy, serie histórica de
ese tipo, último precio— entra por `market`, un objeto que se le pasa. Esa es
la razón de que esté aquí y no dentro del panel: la regla de que un coste medio
se calcula con el cambio del DÍA DE LA COMPRA no depende de Yahoo, y no debería
necesitar a Yahoo para comprobarse.

El contrato de `market` (seis métodos, ninguno con estado que este módulo vea):

    warm(tickers)          calienta cachés; puede no hacer nada
    currency(ticker)       "USD" | "EUR" | ... | "" si no se sabe
    base_factor(ccy)       (divisa base, factor) — GBp -> ("GBP", 0.01)
    fx_series(ccy)         Serie pandas de EUR por unidad cotizada, o None
    fx_now(ccy)            EUR por unidad cotizada hoy, o None
    last_price(ticker)     último precio en divisa nativa, o None

OJO con el contrato de los dos tipos de cambio, que es sutil: devuelven euros
por unidad COTIZADA, con el factor de la divisa YA aplicado. Un instrumento en
GBp cotiza en peniques y el adaptador entrega GBPEUR x 0,01, no la libra
pelada. Por eso `base_factor` no se vuelve a aplicar sobre ellos aquí: sólo
sirve para el atajo de un instrumento que ya está en la divisa base, al que no
hay que pedirle ninguna serie. Aplicarlo dos veces dividiría la posición entre
cien y seguiría pareciendo un número razonable.

La regla que gobierna todo lo demás: una posición sólo se reporta en euros
cuando se conocen SU DIVISA Y SU TIPO. Si falta cualquiera de las dos, los
campos en euros salen a None con un `why` que lo explica — nunca un número
fabricado dando por hecho que el cambio era 1,0.

Los tres movimientos y qué toca cada uno:

    compra      sube cantidad, sube coste
    venta       baja cantidad, baja coste, realiza resultado
    dividendo   NO toca ni cantidad ni coste — es renta cobrada, y va aparte

El dividendo es el que más fácil se cuela mal. Meterlo en el coste bajaría el
precio medio (que es una convención fiscal de otro país, no la española) y
meterlo en el resultado realizado lo mezclaría con las plusvalías, que tributan
por su propia regla. Va a `income`, en su columna, sumando a la rentabilidad
total y a nada más.

DOS RESULTADOS REALIZADOS, y son distintos a propósito:

    realized        coste MEDIO ponderado. Es como se lee una cartera.
    realized_fifo   primera entrada, primera salida. Es el criterio que la ley
                    española aplica a valores homogéneos (LIRPF art. 37.2).

Coinciden exactamente cuando la posición se cierra entera —los dos consumen
todos los lotes— y sólo divergen en una venta PARCIAL. Ahí es donde el número
que enseña una app y el que pide la declaración dejan de ser el mismo, y por
eso se publican los dos en vez de elegir uno y llamarlo «el resultado».
"""
from __future__ import annotations

import pandas as pd

from .parsing import FAR_FUTURE
from .returns import currency_split

BASE_CCY = "EUR"


def compute(movs, market) -> list[dict]:
    """Posiciones valoradas en euros: valor de mercado al cambio de HOY, coste al
    cambio de la fecha de cada compra. `avg_cost` y `last` se quedan en la
    divisa nativa del instrumento.
    """
    tickers = {m["ticker"] for m in movs}
    market.warm(tickers)
    ccy = {t: market.currency(t) for t in tickers}
    fxser = {cu: market.fx_series(cu) for cu in set(ccy.values()) if cu}

    def fx_on(cu, date):
        """EUR por unidad en `date`, o None si no se puede saber."""
        if not cu:
            return None
        base, f = market.base_factor(cu)
        if base == BASE_CCY:
            return f
        s = fxser.get(cu)
        if s is None or not len(s):
            return market.fx_now(cu)              # may be None: that is the answer
        d = pd.to_datetime(date) if date else s.index[-1]
        try:
            if getattr(d, "tzinfo", None) is not None:
                d = d.tz_localize(None)
        except Exception:
            pass
        sub = s[s.index <= d]
        return float(sub.iloc[-1]) if len(sub) else float(s.iloc[0])

    agg = {}
    # Los movimientos sin fecha ordenan los ÚLTIMOS, no los primeros. La cadena
    # vacía va antes que cualquier fecha real, así que un movimiento con la
    # fecha sin parsear se convertía en el más antiguo de la posición y le
    # reseteaba el coste medio en silencio.
    for m in sorted(movs, key=lambda r: (r["date"] or FAR_FUTURE, r["id"])):
        t = m["ticker"]; cu = ccy[t]
        p = agg.setdefault(t, {"ticker": t, "qty": 0.0, "cost_n": 0.0, "cost_e": 0.0,
                               "realized": 0.0, "name": "", "kind": "", "ccy": cu,
                               "oversold": 0.0, "fx_gap": False,
                               "lots": [], "realized_fifo": 0.0,
                               "income": 0.0, "n_div": 0, "inc_gap": False,
                               "fees": 0.0, "withheld": 0.0, "n_ops": 0})
        if m.get("name"):
            p["name"] = m["name"]
        if m.get("kind"):
            p["kind"] = m["kind"]
        q, px, fee = m["quantity"] or 0.0, m["price"] or 0.0, m["fee"] or 0.0
        rate = fx_on(cu, m["date"])                # EUR per unit at the movement date
        if m["side"] == "div":
            # Un dividendo no mueve la posición: ni cantidad ni coste. `fee` es
            # la retención, así que lo cobrado es el bruto menos ella. Su hueco
            # de cambio se apunta APARTE del de compraventa — si no, un
            # dividendo sin tipo se llevaría por delante el realizado entero,
            # que sí se puede calcular.
            p["n_div"] += 1
            if rate is None:
                p["inc_gap"] = True
            else:
                p["income"] += (q * px - fee) * rate
                # En un dividendo `fee` es la RETENCIÓN, no una comisión. Van a
                # cubos distintos porque son cosas distintas: la comisión es un
                # precio que se puede negociar cambiando de bróker; la retención
                # es un impuesto a cuenta, y parte se recupera al declarar.
                p["withheld"] += fee * rate
            continue
        if rate is None:
            p["fx_gap"] = True                     # EUR column is unrecoverable
        else:
            p["fees"] += fee * rate
        p["n_ops"] += 1
        if m["side"] == "buy":
            p["qty"] += q
            p["cost_n"] += q * px + fee
            if rate is not None:
                p["cost_e"] += (q * px + fee) * rate
                # El lote guarda su coste UNITARIO en euros, comisión incluida y
                # al cambio de su día. Es lo único que FIFO necesita recordar, y
                # guardarlo ya dividido evita que una venta parcial tenga que
                # repartir la comisión otra vez.
                p["lots"].append([q, (q * px + fee) * rate / q if q > 1e-12 else 0.0,
                                  m["date"] or ""])
        else:
            # Una venta nunca puede dejar la posición en negativo: eso es un
            # error de datos (una compra que falta, una venta importada dos
            # veces), e inventar participaciones negativas lo esconde detrás de
            # un número que parece plausible.
            sell = min(q, p["qty"]) if p["qty"] > 1e-9 else 0.0
            p["oversold"] += q - sell
            if sell > 1e-9:
                avg_n = p["cost_n"] / p["qty"]
                avg_e = p["cost_e"] / p["qty"] if p["qty"] > 1e-9 else 0.0
                if rate is not None:
                    proceeds = (sell * px - fee) * rate
                    p["realized"] += proceeds - sell * avg_e
                    p["realized_fifo"] += proceeds - _consume_fifo(p["lots"], sell)
                else:
                    _consume_fifo(p["lots"], sell)   # los lotes se gastan igual
                p["qty"] -= sell
                p["cost_n"] -= sell * avg_n
                p["cost_e"] -= sell * avg_e

    out = []
    for t, p in agg.items():
        cu = p["ccy"]; qty = round(p["qty"], 6)
        open_pos = qty > 1e-9
        avg_n = (p["cost_n"] / p["qty"]) if open_pos else None       # native avg cost
        last_n = market.last_price(t) if open_pos else None          # native live price
        rate_now = market.fx_now(cu)
        # Una sola puerta para toda la columna en euros, para que invested,
        # market_value y unreal estén los tres o no esté ninguno — nunca una
        # fila mezclada, que se lee como una pérdida.
        why = (None if not open_pos else
               "moneda desconocida" if not cu else
               "sin tipo de cambio" if rate_now is None or p["fx_gap"] else
               "sin precio" if last_n is None else None)
        valued = open_pos and why is None
        inv_e = p["cost_e"] if valued else None
        mval_e = (qty * last_n * rate_now) if valued else None
        unreal_e = (mval_e - inv_e) if valued else None
        realized = None if p["fx_gap"] else round(p["realized"], 2)
        income = None if p["inc_gap"] else round(p["income"], 2)
        out.append({
            "ticker": t, "name": p.get("name") or "", "kind": p.get("kind") or "",
            "ccy": cu or "?", "qty": qty,
            # `if avg_n` borraría un coste base cero legítimo (un regalo, un
            # spin-off, una posición con todo bonificado) tratándolo como ausente.
            "avg_cost": (round(avg_n, 4) if avg_n is not None else None),
            "last": (round(last_n, 4) if last_n is not None else None),
            "fx": (round(rate_now, 4) if rate_now is not None else None),
            "invested": (round(inv_e, 2) if inv_e is not None else (0.0 if not open_pos else None)),
            "market_value": (round(mval_e, 2) if mval_e is not None else None),
            "unreal": (round(unreal_e, 2) if unreal_e is not None else None),
            "unreal_pct": (round(unreal_e / inv_e * 100, 2)
                           if (unreal_e is not None and inv_e and inv_e > 1e-9) else None),
            # El realizado es una cifra en euros también: si cualquier tramo de
            # esta posición topó con un hueco de cambio, se acumuló con datos
            # parciales, así que se retiene en vez de publicarlo como si
            # estuviera completo.
            "realized": realized,
            "realized_fifo": (None if p["fx_gap"] else round(p["realized_fifo"], 2)),
            # Renta cobrada (dividendos y cupones), neta de retención. Nunca
            # entra en `realized` ni baja el coste: es su propia columna.
            "income": income,
            "n_dividends": p["n_div"],
            "valued": valued, "why": why,
            "oversold": (round(p["oversold"], 6) if p["oversold"] > 1e-9 else 0.0),
            # Lo que has pagado por tener esto, separado de lo que te han
            # retenido. Estaba guardado movimiento a movimiento y enterrado
            # dentro del coste medio: no había ni un sitio donde se sumara.
            "fees": round(p["fees"], 2), "withheld": round(p["withheld"], 2),
            "n_ops": p["n_ops"],
            # Cuánto del resultado lo puso el ACTIVO y cuánto la DIVISA. Es una
            # descomposición exacta, no un reparto aproximado.
            "split": (currency_split(qty, avg_n, last_n, rate_now,
                                     p["cost_n"], p["cost_e"]) if valued else None),
            # Los lotes ABIERTOS, de más antiguo a más nuevo. Se calculaban ya
            # para el FIFO y se tiraban; publicarlos es lo que permite responder
            # «¿qué pasaría si vendiera?» sin recorrer otra vez el libro — y,
            # sobre todo, sin que esa respuesta pueda discrepar del realizado
            # que se enseña al lado, porque sale de los mismos lotes.
            "lots": [{"qty": round(l[0], 6), "unit_cost": round(l[1], 4),
                      "date": (l[2] if len(l) > 2 else None)}
                     for l in p["lots"] if l[0] > 1e-9],
        })
    # El peso se calcula al final, cuando ya se sabe el total, y SÓLO sobre lo
    # que se pudo valorar: repartir 100% incluyendo una posición sin precio
    # diría que pesa cero, que es la lectura contraria a la verdad.
    total = sum(r["market_value"] for r in out if r["market_value"] is not None)
    for r in out:
        r["weight"] = (round(r["market_value"] / total * 100, 2)
                       if (r["market_value"] is not None and total > 1e-9) else None)
    out.sort(key=lambda r: (r["qty"] <= 1e-9, -(r["market_value"] or 0)))
    return out


def _consume_fifo(lots: list, qty: float) -> float:
    """Gasta `qty` unidades de los lotes más ANTIGUOS y devuelve su coste en euros.

    Muta `lots`: un lote consumido a medias se queda con lo que le sobra. La
    lista está ordenada por fecha porque los movimientos se recorren ordenados,
    así que la primera entrada es siempre la primera salida.
    """
    cost = 0.0
    left = qty
    while left > 1e-12 and lots:
        take = min(left, lots[0][0])
        cost += take * lots[0][1]
        lots[0][0] -= take
        left -= take
        if lots[0][0] <= 1e-12:
            lots.pop(0)
    return cost
