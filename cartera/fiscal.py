"""Qué pasaría si vendieras. Aritmética fiscal, no asesoramiento.

Este módulo separa a propósito dos cosas que suelen ir juntas y no valen lo
mismo:

  1. **Lo que sale del libro y es EXACTO**: qué lotes consume una venta por
     FIFO, cuánto costaron, cuánto se ingresa y qué plusvalía resulta. Todo eso
     se deriva de movimientos que ya están apuntados y se puede comprobar.

  2. **Lo que es una ESTIMACIÓN**: el impuesto. Depende de una ley que cambia,
     y sobre todo de cosas que este programa no ve — el resto de tus rentas del
     ahorro del año, tus minusvalías pendientes de ejercicios anteriores, y si
     tributas en territorio foral.

Mezclarlas en un solo número sería lo cómodo y lo peor: convertiría una
estimación en una cifra con el aspecto de un dato. Por eso salen separadas y
por eso los tramos entran por parámetro en vez de estar clavados en el cálculo.

LO QUE ESTE MÓDULO NO SABE, Y NO PUEDE ADIVINAR
-----------------------------------------------
- Tus otras ganancias y pérdidas del año fuera de esta cartera.
- Tus minusvalías pendientes de compensar de los cuatro ejercicios anteriores.
- Si vives en País Vasco o Navarra, que tienen su propio régimen.
- Cualquier particularidad tuya.

Todo eso entra por parámetro. Lo que no se le pasa, no se supone: se declara
que falta.
"""
from __future__ import annotations

# Tramos estatales de la BASE DEL AHORRO del IRPF. Van con su año porque
# cambian, y entran por parámetro para que una reforma se arregle sin tocar la
# aritmética. Cada tramo es (techo, tipo); el último techo es None.
TRAMOS_AHORRO_2025 = [(6000.0, 0.19), (50000.0, 0.21), (200000.0, 0.23),
                      (300000.0, 0.27), (None, 0.30)]
TRAMOS_ANO = 2025

# Recompra dentro de este plazo y la pérdida NO se puede compensar todavía
# (la «regla de los dos meses» para valores cotizados; en no cotizados es un
# año). No se aplica sola: se avisa, porque el programa no sabe si vas a
# recomprar mañana.
RECOMPRA_DIAS_COTIZADO = 60
RECOMPRA_DIAS_NO_COTIZADO = 365


def fifo_preview(lots, qty):
    """Qué lotes consumiría una venta de `qty`, SIN tocar la lista.

    Devuelve `(consumidos, coste, faltan)`. `lots` es la lista de lotes
    abiertos, cada uno `[cantidad, coste_unitario_eur, fecha]`, ya ordenada de
    más antiguo a más nuevo.

    No muta nada a propósito: esto se llama para responder «¿y si…?», y una
    simulación que consume los lotes de verdad convierte una pregunta en una
    operación. `positions.compute` tiene su propia versión destructiva para
    cuando la venta ha ocurrido de verdad.
    """
    restante = float(qty or 0.0)
    if restante <= 0 or not lots:
        return [], 0.0, restante
    consumidos, coste = [], 0.0
    for lote in lots:
        if restante <= 1e-12:
            break
        cant = float(lote[0])
        if cant <= 1e-12:
            continue
        toma = min(restante, cant)
        unit = float(lote[1])
        coste += toma * unit
        consumidos.append({"qty": round(toma, 6), "unit_cost": round(unit, 4),
                           "date": (lote[2] if len(lote) > 2 else None),
                           "cost": round(toma * unit, 2),
                           "partial": toma < cant - 1e-12})
        restante -= toma
    return consumidos, coste, max(0.0, restante)


def _cuota(base, tramos):
    """Impuesto TOTAL sobre una base del ahorro, por tramos."""
    if base <= 0:
        return 0.0
    total, anterior = 0.0, 0.0
    for techo, tipo in tramos:
        tope = base if techo is None else min(base, techo)
        if tope > anterior:
            total += (tope - anterior) * tipo
            anterior = tope
        if techo is not None and base <= techo:
            break
    return total


def tax_on_gain(gain, tramos=None, already=0.0):
    """Impuesto que AÑADE una ganancia sobre una base que ya tenía `already`.

    Es la diferencia entre la cuota con y sin ella, no el tipo del primer
    tramo. Calcularlo como `gain x 19%` es el error clásico: quien ya tiene
    plusvalías del año paga la nueva ganancia en su tramo, no en el más bajo.
    """
    tramos = tramos or TRAMOS_AHORRO_2025
    if gain is None or gain <= 0:
        return 0.0
    already = max(0.0, float(already or 0.0))
    return _cuota(already + gain, tramos) - _cuota(already, tramos)


def simulate_sale(lots, qty, price, fee=0.0, fx=1.0, *, tramos=None,
                  other_gains=0.0, pending_losses=0.0):
    """Qué dejaría una venta: lo exacto y lo estimado, separados.

    `price` va en divisa nativa y `fx` son los euros por unidad de HOY: el
    ingreso se valora al cambio del día en que se vendería, igual que hace la
    contabilidad real cuando la venta ocurre.

    `pending_losses` son minusvalías pendientes de compensar. Reducen la base,
    nunca por debajo de cero, y lo que sobra sigue pendiente.
    """
    qty = float(qty or 0.0)
    consumidos, coste, faltan = fifo_preview(lots, qty)
    vendidas = qty - faltan
    ingreso = (vendidas * float(price) - float(fee or 0.0)) * float(fx)
    resultado = ingreso - coste

    usadas = min(max(0.0, float(pending_losses or 0.0)), max(0.0, resultado))
    base = max(0.0, resultado - usadas)
    impuesto = tax_on_gain(base, tramos, already=other_gains)

    return {
        # ── del libro, exacto ──────────────────────────────────────────────
        "qty": round(vendidas, 6),
        "short": round(faltan, 6),
        "proceeds": round(ingreso, 2),
        "cost_fifo": round(coste, 2),
        "result": round(resultado, 2),
        "lots": consumidos,
        # ── estimación ────────────────────────────────────────────────────
        "losses_used": round(usadas, 2),
        "losses_left": round(max(0.0, float(pending_losses or 0.0)) - usadas, 2),
        "taxable_base": round(base, 2),
        "tax": round(impuesto, 2),
        "net": round(ingreso - impuesto, 2),
        "effective_rate": (round(impuesto / resultado * 100, 2)
                           if resultado > 1e-9 else None),
        "other_gains": round(max(0.0, float(other_gains or 0.0)), 2),
    }


def loss_offset_note(result):
    """Si la venta da pérdida, qué se puede hacer con ella. Texto, no número."""
    if result is None or result >= 0:
        return None
    return ("Una minusvalía compensa primero otras ganancias patrimoniales del "
            "mismo ejercicio; lo que sobre se puede aplicar contra rendimientos "
            "del capital mobiliario con un límite, y el resto queda pendiente "
            "para los cuatro ejercicios siguientes.")


def repurchase_risk(sell_date, buy_dates, listed=True):
    """Compras del MISMO valor cerca de la venta, que bloquearían la pérdida.

    La ley no deja computar una minusvalía si se recompra el mismo valor dentro
    de los dos meses anteriores o posteriores (un año si no cotiza). Este módulo
    sólo puede mirar hacia ATRÁS —lo que ya está apuntado—; hacia delante no
    sabe si vas a recomprar, y por eso avisa en vez de decidir.
    """
    if not sell_date or not buy_dates:
        return []
    dias = RECOMPRA_DIAS_COTIZADO if listed else RECOMPRA_DIAS_NO_COTIZADO
    from datetime import date

    def parse(s):
        try:
            y, m, d = str(s)[:10].split("-")
            return date(int(y), int(m), int(d))
        except Exception:
            return None

    v = parse(sell_date)
    if v is None:
        return []
    cerca = []
    for b in buy_dates:
        f = parse(b)
        if f is None:
            continue
        delta = abs((v - f).days)
        if delta <= dias:
            cerca.append({"date": str(b)[:10], "days": delta})
    return sorted(cerca, key=lambda x: x["days"])
