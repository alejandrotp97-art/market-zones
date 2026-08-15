"""Look-through geographic exposure of the portfolio.

Where the money is, not where the paperwork is. Four of the five holdings are
funds, so colouring a country by the instrument's domicile would paint Ireland
and France at ~97% and say nothing true. Each fund is instead decomposed into
the geography of what it holds, using the curated table in
`data/country_weights.json` (see `analysis/seed_country_weights.py`).

The one rule the whole module serves: **the map may only show exposure the
portfolio actually supports.** Everything that cannot be placed on a country —
bullion, an instrument missing from the table, the aggregated remainder the
source did not name, a position with no price — is reported as its own figure
rather than dropped or spread across the countries that are known. A total that
reaches 100% by redistribution is a lie that looks like precision.
"""
from __future__ import annotations

import json
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
TABLE_PATH = os.path.join(_HERE, "data", "country_weights.json")

ASSET_CLASSES = ("equity", "bond", "commodity")

# Display names for the countries the table can produce. A code with no name
# falls back to the code itself — an unfamiliar label is a small problem; a
# country dropped for want of a translation is a wrong map.
COUNTRY_ES = {
    "US": "Estados Unidos", "JP": "Japón", "GB": "Reino Unido", "CA": "Canadá",
    "DE": "Alemania", "FR": "Francia", "CH": "Suiza", "NL": "Países Bajos",
    "AU": "Australia", "IE": "Irlanda", "SE": "Suecia", "DK": "Dinamarca",
    "IT": "Italia", "ES": "España", "FI": "Finlandia", "BE": "Bélgica",
    "NO": "Noruega", "AT": "Austria", "PT": "Portugal", "IL": "Israel",
    "NZ": "Nueva Zelanda", "SG": "Singapur", "HK": "Hong Kong",
    "LU": "Luxemburgo", "PL": "Polonia", "GR": "Grecia", "CZ": "Chequia",
    "HU": "Hungría", "TW": "Taiwán", "KR": "Corea del Sur", "CN": "China",
    "IN": "India", "BR": "Brasil", "ZA": "Sudáfrica", "SA": "Arabia Saudí",
    "MX": "México", "MY": "Malasia", "TH": "Tailandia", "ID": "Indonesia",
    "TR": "Turquía", "PH": "Filipinas", "CL": "Chile", "QA": "Catar",
    "AE": "Emiratos Árabes Unidos", "KW": "Kuwait", "PE": "Perú",
    "CO": "Colombia", "EG": "Egipto", "RU": "Rusia", "AR": "Argentina",
    "VN": "Vietnam", "PK": "Pakistán", "RO": "Rumanía", "KZ": "Kazajistán",
    "CY": "Chipre", "BM": "Bermudas", "KY": "Islas Caimán", "JE": "Jersey",
    "GG": "Guernsey", "MO": "Macao", "UY": "Uruguay", "IS": "Islandia",
    "SI": "Eslovenia", "SK": "Eslovaquia", "EE": "Estonia", "LV": "Letonia",
    "LT": "Lituania", "MT": "Malta", "HR": "Croacia", "BG": "Bulgaria",
}

_table_cache: tuple[float, dict] | None = None
_table_lock = threading.Lock()


def country_name(iso2: str) -> str:
    return COUNTRY_ES.get(iso2, iso2)


def load_table(path: str = TABLE_PATH) -> dict:
    """The curated weights, re-read when the file changes on disk.

    Seeding rewrites the file under a running service, so the mtime is checked
    rather than caching forever: the alternative is a restart being the only way
    to see refreshed weights.
    """
    global _table_cache
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"as_of": None, "instruments": {}}
    with _table_lock:
        if _table_cache and _table_cache[0] == mtime:
            return _table_cache[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # A malformed table must not take the portfolio page down with it. The
        # caller sees an empty table, every position lands in `unmapped`, and
        # the page says so.
        return {"as_of": None, "instruments": {}}
    with _table_lock:
        _table_cache = (mtime, data)
    return data


def country_exposure(positions, table: dict, asset_class: str | None = None) -> dict:
    """Split each position's EUR market value over its countries.

    `positions` are the rows `_cartera_payload` already builds. Only those with
    `valued` True carry a trustworthy `market_value`, so only those contribute;
    the rest come back in `excluded` with the reason the portfolio gave.

    `asset_class` filters to one of ASSET_CLASSES, or None for everything. The
    filter partitions the total: equity + bond + commodity always sums back to
    the unfiltered figure, so switching the toggle never invents or loses money.

    Percentages are of `mapped_eur` — the money actually placed on a country —
    never of the portfolio. Dividing by the portfolio would shrink every country
    below what the fund's own factsheet reports and make the two irreconcilable.
    """
    instruments = table.get("instruments", {}) or {}

    by_country: dict[str, dict] = {}
    excluded, unmapped = [], []
    mapped = other = no_geo = unmapped_eur = 0.0

    for p in positions:
        tk = p.get("ticker")
        spec = instruments.get(tk)

        # The class filter runs BEFORE the valuation and coverage checks: a bond
        # fund is simply not part of an equity view, and reporting it there as
        # "excluded" or "unmapped" would be noise, not information.
        if asset_class is not None:
            if not spec or spec.get("asset_class") != asset_class:
                continue

        if not p.get("valued"):
            excluded.append({"ticker": tk, "why": p.get("why") or "sin valorar"})
            continue

        mval = p.get("market_value")
        if mval is None:                      # belt and braces: valued but no number
            excluded.append({"ticker": tk, "why": "sin valor de mercado"})
            continue
        mval = float(mval)

        if not spec:
            unmapped.append({"ticker": tk, "name": p.get("name") or tk,
                             "eur": round(mval, 2), "why": "sin tabla de países"})
            unmapped_eur += mval
            continue

        if spec.get("no_geography"):
            # Bullion. Not a gap in the data — a property of the asset.
            no_geo += mval
            continue

        weights = spec.get("weights") or {}
        if not weights:
            # Two different states that must not read the same. "Pending a
            # reference" is a job someone can finish; "no weights" is a table
            # that came back empty and needs looking at.
            why = ("pendiente de elegir ETF de referencia" if spec.get("needs_proxy")
                   else "tabla sin pesos")
            unmapped.append({"ticker": tk, "name": p.get("name") or tk,
                             "eur": round(mval, 2), "why": why})
            unmapped_eur += mval
            continue

        for iso2, pct in weights.items():
            eur = mval * float(pct) / 100.0
            if eur <= 0:
                continue
            slot = by_country.setdefault(
                iso2, {"iso2": iso2, "name": country_name(iso2),
                       "eur": 0.0, "contributors": []})
            slot["eur"] += eur
            slot["contributors"].append(
                {"ticker": tk, "name": p.get("name") or tk, "eur": round(eur, 2)})
            mapped += eur

        # The share the source aggregated as "Other" and never named. It is
        # carried as its own figure: splitting it over the named countries would
        # put money in places the factsheet never claimed.
        other += mval * float(spec.get("other") or 0.0) / 100.0

    countries = sorted(by_country.values(), key=lambda c: -c["eur"])
    for c in countries:
        c["pct"] = round(c["eur"] / mapped * 100.0, 4) if mapped > 0 else 0.0
        c["eur"] = round(c["eur"], 2)
        c["contributors"].sort(key=lambda x: -x["eur"])

    total = mapped + other + no_geo + unmapped_eur
    return {
        "as_of": table.get("as_of"),
        "asset_class": asset_class,
        "countries": countries,
        # Every euro the portfolio could value, in exactly one bucket.
        "mapped_eur": round(mapped, 2),
        "other_eur": round(other, 2),
        "no_geography_eur": round(no_geo, 2),
        "unmapped_eur": round(unmapped_eur, 2),
        "total_eur": round(total, 2),
        "unmapped": unmapped,
        "excluded": excluded,
        "sources": _sources(instruments),
    }


def _sources(instruments: dict) -> list:
    """What the UI needs to say where the weights came from, and how old."""
    out = []
    for tk, spec in instruments.items():
        proxy = spec.get("proxy")
        out.append({"ticker": tk, "name": spec.get("name") or tk,
                    "asset_class": spec.get("asset_class"),
                    "proxy": (proxy or {}).get("name"),
                    "proxy_note": (proxy or {}).get("note") or spec.get("note"),
                    "source": spec.get("source"), "as_of": spec.get("as_of")})
    out.sort(key=lambda x: x["name"])
    return out
