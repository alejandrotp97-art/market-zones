"""Sectores y X-ray: cada fondo abierto en lo que tiene dentro.

Las dos lecturas salen de la tabla que siembra `analysis/seed_country_weights.py`
desde la ficha pública de justETF (la misma que da los países del mapa):

  * `sectors` / `sectors_other`: el reparto sectorial del fondo, con el resto
    que la fuente agrega y no nombra;
  * `holdings`: sus diez mayores posiciones, con ISIN y peso.

La regla es la del mapa: **sólo se enseña la exposición que la tabla respalda.**
El oro no tiene sector, un fondo sembrado antes de que existieran los sectores
todavía no los tiene, y el resto agregado no se reparte: cada cosa sale como su
propia cifra con su motivo.

El X-ray sólo ve el TOP 10 de cada fondo — en un MSCI World es ~27 % del fondo.
Por eso los solapes entre fondos son una COTA INFERIOR, y la respuesta dice
cuánto dinero ha podido ver (`seen_eur`) frente al total de la cartera.
"""
from __future__ import annotations

from itertools import combinations

from cartera.parsing import symbol_isin

SECTOR_ES = {
    "Technology": "Tecnología", "Finance": "Finanzas", "Industrials": "Industria",
    "Healthcare": "Salud", "Consumer Non-Cyclicals": "Consumo básico",
    "Energy": "Energía", "Non-Energy Materials": "Materiales",
    "Consumer Cyclicals": "Consumo cíclico", "Utilities": "Servicios públicos",
    "Consumer Services": "Servicios al consumidor",
    "Telecommunication": "Telecomunicaciones",
    "Business Services": "Servicios empresariales",
    "Non-Corporate": "No corporativo", "Real Estate": "Inmobiliario",
}


def sector_name(key: str) -> str:
    return SECTOR_ES.get(key, key)


def _valued(positions):
    """(posiciones con valor, excluidas con motivo)."""
    ok, excluded = [], []
    for p in positions:
        mv = p.get("market_value")
        if not p.get("valued") or mv is None:
            excluded.append({"ticker": p.get("ticker"), "why": p.get("why") or "sin valorar"})
            continue
        ok.append((p, float(mv)))
    return ok, excluded


def sector_exposure(positions, table: dict) -> dict:
    """Reparte el valor de mercado de cada posición entre sus sectores.

    Los % son sobre `mapped_eur` —lo que de verdad se ha podido colocar en un
    sector—, igual que la ficha del fondo: dividir por la cartera entera haría
    que ningún sector cuadrase con lo que publica la gestora.
    """
    inst = table.get("instruments", {}) or {}
    ok, excluded = _valued(positions)
    by, outside = {}, []
    mapped = other = outside_eur = 0.0
    for p, mv in ok:
        tk = p.get("ticker")
        spec = inst.get(tk)
        name = p.get("name") or tk
        why = None
        if not spec:
            why = "sin ficha del fondo"
        elif spec.get("no_geography"):
            why = "no tiene sector"
        elif not spec.get("sectors"):
            why = spec.get("sectors_error") or "sin sectores todavía"
        if why:
            outside.append({"ticker": tk, "name": name, "eur": round(mv, 2), "why": why})
            outside_eur += mv
            continue
        for key, pct in spec["sectors"].items():
            eur = mv * float(pct) / 100.0
            if eur <= 0:
                continue
            slot = by.setdefault(key, {"key": key, "name": sector_name(key),
                                       "eur": 0.0, "contributors": []})
            slot["eur"] += eur
            slot["contributors"].append({"ticker": tk, "name": name, "eur": round(eur, 2)})
            mapped += eur
        other += mv * float(spec.get("sectors_other") or 0.0) / 100.0

    sectors = sorted(by.values(), key=lambda s: -s["eur"])
    for s in sectors:
        s["pct"] = s["eur"] / mapped * 100.0 if mapped > 0 else 0.0
        s["eur"] = round(s["eur"], 2)
        s["contributors"].sort(key=lambda c: -c["eur"])
    return {"sectors": sectors, "mapped_eur": round(mapped, 2),
            "other_eur": round(other, 2), "outside": outside,
            "outside_eur": round(outside_eur, 2), "excluded": excluded}


def xray(positions, table: dict, top: int = 25) -> dict:
    """Las acciones de debajo de los fondos, sumadas por ISIN en toda la cartera.

    Una acción que tienes directamente también entra (al 100 % de sí misma), así
    que «Apple por tu cuenta + Apple dentro del MSCI World» sale como una sola
    fila con las dos vías. `shared` son las que llegan por más de un sitio.
    `pairs` es el solape entre cada par de fondos: la suma de los mínimos de los
    pesos comunes, en puntos de su top 10.
    """
    inst = table.get("instruments", {}) or {}
    ok, excluded = _valued(positions)
    portfolio = sum(mv for _, mv in ok)
    agg, without, funds = {}, [], {}
    seen = 0.0

    def add(isin, name, eur, via):
        nonlocal seen
        slot = agg.setdefault(isin, {"isin": isin, "name": name, "eur": 0.0, "via": []})
        slot["eur"] += eur
        slot["via"].append(via)
        seen += eur

    for p, mv in ok:
        tk = p.get("ticker")
        name = p.get("name") or tk
        spec = inst.get(tk) or {}
        hold = spec.get("holdings") or []
        if hold:
            funds[tk] = {"name": name, "w": {h["isin"]: h for h in hold}}
            for h in hold:
                eur = mv * float(h["pct"]) / 100.0
                add(h["isin"], h["name"], eur,
                    {"ticker": tk, "name": name, "pct": h["pct"], "eur": round(eur, 2),
                     "direct": False})
            continue
        isin = symbol_isin(tk) if p.get("kind") == "Acción" else None
        if isin:
            add(isin, name, mv, {"ticker": tk, "name": name, "pct": 100.0,
                                 "eur": round(mv, 2), "direct": True})
        elif not spec.get("no_geography"):
            without.append({"ticker": tk, "name": name, "eur": round(mv, 2)})

    rows = sorted(agg.values(), key=lambda h: -h["eur"])
    for h in rows:
        h["eur"] = round(h["eur"], 2)
        h["pct"] = h["eur"] / portfolio * 100.0 if portfolio > 0 else 0.0
        h["via"].sort(key=lambda v: -v["eur"])
    shared = [h for h in rows if len(h["via"]) >= 2]

    pairs = []
    for a, b in combinations(sorted(funds), 2):
        wa, wb = funds[a]["w"], funds[b]["w"]
        common = [i for i in wa if i in wb]
        if not common:
            continue
        common.sort(key=lambda i: -min(wa[i]["pct"], wb[i]["pct"]))
        pairs.append({"a": a, "b": b, "a_name": funds[a]["name"], "b_name": funds[b]["name"],
                      "overlap_pct": round(sum(min(wa[i]["pct"], wb[i]["pct"]) for i in common), 4),
                      "common": [agg[i]["name"] for i in common]})
    pairs.sort(key=lambda x: -x["overlap_pct"])

    return {"holdings": rows[:top], "shared": shared, "pairs": pairs,
            "without": without, "excluded": excluded,
            "portfolio_eur": round(portfolio, 2), "seen_eur": round(seen, 2)}


__all__ = ["SECTOR_ES", "sector_exposure", "sector_name", "xray"]
