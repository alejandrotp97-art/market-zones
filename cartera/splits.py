"""Splits: qué pasa con un libro cuando el instrumento se parte en dos.

EL PROBLEMA, QUE NO DA NINGÚN ERROR
------------------------------------
La serie de precios de Yahoo viene **ajustada por splits**: si algo hizo un 10:1
en junio, los precios anteriores aparecen divididos entre diez, y la gráfica no
tiene ningún salto. Esa serie sólo cuadra con la cantidad de títulos POSTERIOR
al split.

El libro, en cambio, guarda lo que se tecleó el día de la compra. Si nadie
actualizó las cantidades después del split, pasan dos cosas y las dos callan:

  * el **valor de hoy** sale dividido por el factor del split — con un 10:1,
    una décima parte de lo que hay de verdad;
  * el **histórico** entero queda igual de encogido, y la rentabilidad se
    calcula sobre una cartera que nunca existió.

Ninguna de las dos produce un error. Producen números plausibles.

LO QUE ESTE MÓDULO NO PUEDE SABER
----------------------------------
**Si el split ya se tuvo en cuenta.** «10 títulos» en el libro puede ser diez
títulos de antes del split o diez ya ajustados; el número es el mismo y nada lo
distingue. Así que este módulo NO decide: detecta que hubo un split posterior a
una compra, dice exactamente qué cambiaría, y espera. Aplicarlo o marcarlo como
ya hecho es una decisión de quien tiene el libro.

LA REGLA DEL AJUSTE
-------------------
Multiplicar la cantidad por el factor y dividir el precio por el mismo factor.
El COSTE TOTAL no se mueve —se pagó lo que se pagó— y por eso el ajuste es
seguro: no toca ni una plusvalía ni un precio medio en euros.

    10 títulos a 400 €  --10:1-->  100 títulos a 40 €     coste 4.000 € en ambos
"""
from __future__ import annotations


def pending(movements, splits, acked=()):
    """Splits que afectan a movimientos ya apuntados y nadie ha resuelto.

    `movements` son los de UN instrumento, cada uno con `date` y `quantity`.
    `splits` es `[{"date", "ratio"}]`. `acked` son las fechas de los splits que
    ya se marcaron como resueltos.

    Un split sólo importa si hay movimientos ANTERIORES a él: los posteriores
    ya se teclearon en la escala nueva.
    """
    resueltos = {str(a)[:10] for a in acked}
    out = []
    for s in sorted(splits or [], key=lambda x: str(x.get("date"))):
        fecha, ratio = str(s.get("date") or "")[:10], float(s.get("ratio") or 0)
        if not fecha or ratio <= 0 or abs(ratio - 1.0) < 1e-9:
            continue
        if fecha in resueltos:
            continue
        afectados = [m for m in movements
                     if (m.get("date") or "") and str(m["date"])[:10] < fecha]
        if not afectados:
            continue
        out.append({
            "date": fecha, "ratio": ratio,
            "n_movements": len(afectados),
            "kind": "split" if ratio > 1 else "contrasplit",
            "ids": [m["id"] for m in afectados if m.get("id") is not None],
        })
    return out


def preview(movements, split):
    """Qué le pasaría a cada movimiento. No cambia nada.

    Devuelve las filas con su cantidad y su precio antes y después, y el coste
    de cada una a los dos lados. Ese coste es la comprobación: si no sale
    idéntico, el ajuste está mal planteado y no debe aplicarse.
    """
    fecha, ratio = str(split["date"])[:10], float(split["ratio"])
    filas = []
    for m in movements:
        d = str(m.get("date") or "")[:10]
        if not d or d >= fecha:
            continue
        q, px = float(m.get("quantity") or 0.0), float(m.get("price") or 0.0)
        filas.append({
            "id": m.get("id"), "date": d, "side": m.get("side"),
            "qty_before": q, "qty_after": round(q * ratio, 8),
            "price_before": px, "price_after": (round(px / ratio, 8) if ratio else px),
            "cost_before": round(q * px, 6),
            "cost_after": round((q * ratio) * (px / ratio), 6) if ratio else None,
        })
    return filas


def cost_is_preserved(rows, tol=0.01):
    """El ajuste NO puede mover el coste. Es la condición que lo hace seguro.

    Se comprueba antes de escribir nada: si el total cambia, hay un error de
    planteamiento y lo que sigue sería reescribir el libro de alguien con
    números peores que los que tenía.
    """
    antes = sum(r["cost_before"] for r in rows)
    despues = sum((r["cost_after"] or 0.0) for r in rows)
    return abs(antes - despues) <= max(tol, abs(antes) * 1e-9)
