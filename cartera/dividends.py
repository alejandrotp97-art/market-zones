"""Dividendos: lo cobrado mes a mes y una previsión de los próximos doce meses.

LO COBRADO sale de la reconstrucción diaria (`_reconstruct_portfolio`): ahí cada
reparto ya está en euros, neto de retención y al cambio de SU día, que es la
misma cifra que usa la rentabilidad. Contarlo otra vez por otro camino sería
abrir la puerta a que la tarjeta y el TWR discrepen.

LA PREVISIÓN repite, doce meses después, cada pago que el instrumento hizo en
los últimos doce meses naturales, por las participaciones de HOY y al cambio de
HOY. Es la regla más simple que no inventa nada:

  * un instrumento que dejó de pagar hace más de un año no proyecta nada;
  * no se extrapola ningún crecimiento del dividendo;
  * la cifra es BRUTA por acción tal y como la publica la fuente — la retención
    depende del país del emisor y de tu situación, y no se adivina.

Es una estimación, y quien la pinte tiene que decirlo.
"""
from __future__ import annotations

import datetime as dt


def _month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    k = year * 12 + (month - 1) + n
    return k // 12, k % 12 + 1


def forecast(holdings: dict, events: dict, today: dt.date) -> dict:
    """Previsión de los 12 meses naturales siguientes al actual.

    `holdings[ticker]` = {qty, fx (EUR por unidad de cotización, o None), name,
    market_value}. `events[ticker]` = [{date: "YYYY-MM-DD", amount}] por acción,
    en la divisa de cotización.

    Los pagos de los meses M−11..M (M = el mes de `today`) se proyectan a
    M+1..M+12: el mismo mes del año siguiente.
    """
    months = [_add_months(today.year, today.month, i) for i in range(1, 13)]
    slots = {f"{y:04d}-{m:02d}": {"month": f"{y:04d}-{m:02d}", "eur": 0.0, "by": []}
             for y, m in months}
    first_past = _add_months(today.year, today.month, -11)
    per_t, no_fx = {}, []
    for t, h in holdings.items():
        evs = events.get(t) or []
        qty = float(h.get("qty") or 0.0)
        if qty <= 1e-12 or not evs:
            continue
        recent = []
        for e in evs:
            d = dt.date.fromisoformat(str(e["date"])[:10])
            if first_past <= (d.year, d.month) <= (today.year, today.month) and e.get("amount"):
                recent.append((d, float(e["amount"])))
        if not recent:
            continue
        if h.get("fx") is None:
            no_fx.append(t)
            continue
        for d, amount in recent:
            key = _month_key(dt.date(d.year + 1, d.month, 1))
            eur = amount * qty * float(h["fx"])
            slots[key]["eur"] += eur
            slots[key]["by"].append({"ticker": t, "name": h.get("name") or t,
                                     "eur": round(eur, 2), "per_share": amount})
            per_t[t] = per_t.get(t, 0.0) + eur

    out_months = []
    for y, m in months:
        s = slots[f"{y:04d}-{m:02d}"]
        s["eur"] = round(s["eur"], 2)
        s["by"].sort(key=lambda x: -x["eur"])
        out_months.append(s)
    by_ticker = []
    for t, eur in sorted(per_t.items(), key=lambda kv: -kv[1]):
        mv = holdings[t].get("market_value")
        by_ticker.append({"ticker": t, "name": holdings[t].get("name") or t,
                          "eur": round(eur, 2),
                          "yield": (eur / float(mv)) if mv else None})
    return {"months": out_months, "total": round(sum(per_t.values()), 2),
            "by_ticker": by_ticker, "no_fx": sorted(no_fx)}


def monthly_received(idx, pos: dict, names: dict, months: int = 24) -> list[dict]:
    """[{month, eur, by: [{ticker, name, eur}]}] de los últimos `months` meses.

    Meses contiguos desde el primero con algún cobro hasta el último del
    histórico: un mes sin cobros sale a 0 — aquí el cero SÍ es un dato.
    """
    if not len(idx):
        return []
    acc = {}
    for t, p in pos.items():
        for d, v in zip(idx, p["divs"], strict=True):
            if v > 1e-9:
                slot = acc.setdefault(_month_key(d), {})
                slot[t] = slot.get(t, 0.0) + float(v)
    if not acc:
        return []
    last = idx[-1]
    floor = _add_months(last.year, last.month, -(months - 1))
    y, m = max(min(tuple(map(int, k.split("-"))) for k in acc), floor)
    out = []
    while (y, m) <= (last.year, last.month):
        k = f"{y:04d}-{m:02d}"
        by = sorted(({"ticker": t, "name": names.get(t, t), "eur": round(v, 2)}
                     for t, v in acc.get(k, {}).items()), key=lambda x: -x["eur"])
        out.append({"month": k, "eur": round(sum(b["eur"] for b in by), 2), "by": by})
        y, m = _add_months(y, m, 1)
    return out


__all__ = ["forecast", "monthly_received"]
