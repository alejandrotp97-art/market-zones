"""Rentabilidad de cada posición por tramos: 1D / 1S / 1M / YTD / 1A / Todo.

Lo que se mide en cada tramo es la GANANCIA en euros, no la variación del valor:

    ganancia = valor_final − valor_inicial − flujos_netos_del_tramo + dividendos

Comprar 1000 € sube el valor 1000 € sin haber ganado nada, y vender lo baja sin
haber perdido nada; restar los flujos es lo que separa una cosa de la otra. Una
posición abierta a mitad de tramo entra con valor inicial cero y su compra como
flujo, así que su ganancia arranca en la compra y no antes.

El porcentaje es esa ganancia sobre el capital expuesto en el tramo —el valor al
abrirlo más lo comprado dentro—. Es un rendimiento simple, no un TWR: para una
posición suelta es la cifra que se espera ver («he ganado 50 € sobre 500 €»), y
el TWR de la cartera entera ya lo da el panel de rentabilidad.

Todo llega en euros y alineado a las mismas sesiones (`_reconstruct_portfolio`).
Este módulo no sabe de red, de divisas ni de Flask.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOWS = ("1d", "1w", "1m", "ytd", "1y", "all")
_DAYS = {"1w": 7, "1m": 30, "1y": 365}


def _start_index(idx: pd.DatetimeIndex, window: str) -> int | None:
    """Índice del último cierre ANTERIOR al tramo; None = desde el principio.

    Mismo convenio que `_resolver_ventana`: para medir lo que hizo enero hace
    falta el cierre del 31 de diciembre. Si el histórico es más corto que el
    tramo, el tramo es todo el histórico.
    """
    n = len(idx)
    if window == "all" or n < 2:
        return None
    if window == "1d":
        return n - 2
    end = idx[-1]
    if window == "ytd":
        start = pd.Timestamp(year=end.year, month=1, day=1)
    else:
        start = end - pd.Timedelta(days=_DAYS[window])
    i0 = int(idx.searchsorted(start, side="left")) - 1
    return i0 if i0 >= 0 else None


def _one(pos: dict, i0: int | None, i1: int | None = None) -> tuple[float, float]:
    """(ganancia, capital expuesto) de una posición en (i0, i1]; i1 None = el final."""
    value, flows, divs = pos["value"], pos["flows"], pos["divs"]
    hi = len(value) - 1 if i1 is None else i1
    lo = 0 if i0 is None else i0 + 1
    v0 = 0.0 if i0 is None else float(value[i0])
    fl = flows[lo:hi + 1]
    gain = float(value[hi]) - v0 - float(fl.sum()) + float(divs[lo:hi + 1].sum())
    exposed = v0 + float(fl[fl > 0].sum())
    return gain, exposed


def range_returns(pos: dict, i0: int | None, i1: int) -> dict:
    """{ticker: {abs, pct}} de cada posición que hizo ALGO en (i0, i1].

    A diferencia de `period_returns`, aquí sí entra lo vendido dentro del rango:
    en el resumen de un mes, una posición cerrada a mitad de mes también ganó o
    perdió ese mes. Lo que no tuvo ni valor ni movimiento, fuera.
    """
    lo = 0 if i0 is None else i0
    out = {}
    for t, p in pos.items():
        active = (np.abs(p["value"][lo:i1 + 1]).sum() > 1e-9
                  or np.abs(p["flows"][lo:i1 + 1]).sum() > 1e-9
                  or np.abs(p["divs"][lo:i1 + 1]).sum() > 1e-9)
        if active:
            out[t] = _cell(*_one(p, i0, i1))
    return out


def _cell(gain: float, exposed: float) -> dict:
    return {"abs": round(gain, 2),
            "pct": (gain / exposed) if exposed > 1e-9 else None}


def period_returns(idx, pos: dict, windows=WINDOWS) -> dict:
    """{windows, positions: {ticker: {window: {abs, pct}}}, total: {window: …}}.

    `pos[ticker]` = {value, flows, divs}: arrays en euros alineados a `idx`.
    `flows` es la caja que entra en la posición (compra +, venta −); `divs`, los
    dividendos netos cobrados ese día. Las posiciones que ya no valen nada al
    final del histórico —vendidas— no salen: no tienen tramo que enseñar.
    """
    idx = pd.DatetimeIndex(idx)
    starts = {w: _start_index(idx, w) for w in windows}
    out, tot = {}, {w: [0.0, 0.0] for w in windows}
    for t, p in pos.items():
        if not len(p["value"]) or float(p["value"][-1]) <= 1e-9:
            continue
        row = {}
        for w in windows:
            g, e = _one(p, starts[w])
            row[w] = _cell(g, e)
            tot[w][0] += g
            tot[w][1] += e
        out[t] = row
    return {"windows": list(windows), "positions": out,
            "total": {w: _cell(*tot[w]) for w in windows},
            "as_of": idx[-1].date().isoformat() if len(idx) else None}


__all__ = ["WINDOWS", "period_returns", "range_returns"]

