#!/usr/bin/env python3
"""Seed `data/country_weights.json` — the look-through country table.

    python3 analysis/seed_country_weights.py            # every holding
    python3 analysis/seed_country_weights.py --only IE000M7V94E1.SG
    python3 analysis/seed_country_weights.py --quiet     # for the timer

Runs OUTSIDE the request path — from a timer, from a one-shot background
refresh, or by hand. The dashboard only ever reads the JSON this writes, so a
justETF markup change breaks here, where it is visible, instead of inside a page
load.

WHY A TABLE AT ALL
Most holdings are funds. Colouring a country by where the fund is domiciled
would paint Ireland and France at ~97% and describe the paperwork rather than
the portfolio. The map needs look-through: each fund decomposed into the
geography of what it actually holds. No free API publishes that.

WHERE THE HOLDINGS COME FROM
The portfolio database, not a list in this file — a holding bought yesterday has
to be seedable today without anyone editing source. Two kinds of ticker:

  * an ISIN-shaped symbol (`IE000M7V94E1.SG`) is a listed ETF; justETF profiles
    it directly and the whole thing resolves with no human judgement.
  * a Yahoo mutual-fund symbol (`0P0001CLDK.F`) is not listed anywhere justETF
    indexes. It needs a PROXY — an ETF on the same index — and picking one is a
    judgement call, so it is made once, by a person, in PROXIES below. An
    unknown fund is recorded as `needs_proxy` rather than matched to whatever a
    search happens to return first: a plausible wrong ETF is a different share
    class of a different index, and nothing downstream would ever notice.

COVERAGE
justETF publishes the top ten countries plus an aggregated "Other" (~9% on a
world index). The remainder is NOT redistributed — it is written as an explicit
`other` figure and surfaced as "Otros países". Spreading it across the named
countries would invent exposure that was never reported.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import http.cookiejar
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "country_weights.json")
CARTERA_DB = os.path.join(ROOT, "cartera.db")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")

# ── Hand-picked references for holdings justETF cannot profile ────────────
# Keyed by the ticker the portfolio stores. `proxy_isin: None` means the asset
# genuinely has no geography — a fact about the asset, not a missing lookup.
PROXIES = {
    "0P0001CLDK.F": {
        "asset_class": "equity", "proxy_isin": "IE00B4L5Y983",
        "proxy_name": "iShares Core MSCI World UCITS ETF",
        "proxy_note": "mismo índice (MSCI World), distinto vehículo"},
    "0P00012I6A.F": {
        "asset_class": "equity", "proxy_isin": "IE00B0M63177",
        "proxy_name": "iShares MSCI EM UCITS ETF",
        "proxy_note": "mismo índice (MSCI Emerging Markets)"},
    "0P0000CV2T.F": {
        "asset_class": "bond", "proxy_isin": "IE00BSKRJX20",
        "proxy_name": "iShares EUR Government Bond 20yr Target Duration UCITS ETF",
        "proxy_note": "mismo segmento (deuda soberana euro larga), no el mismo índice"},
    "FR0013416716.SG": {
        "asset_class": "commodity", "proxy_isin": None,
        "proxy_name": None,
        "proxy_note": "el oro físico no tiene país de exposición"},
}

# Asset class for auto-discovered tickers. justETF does not label this in a form
# worth parsing, and guessing "equity" for a bond ETF would silently mix
# sovereign debt into the equity view — the exact confusion the filter exists to
# prevent. Unknown stays unknown until a person says otherwise.
DEFAULT_CLASS = "equity"

ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")

# ── Country name -> ISO 3166-1 alpha-2 ────────────────────────────────────
# justETF writes country names in English. Deliberately a closed table: an
# unknown name FAILS that instrument instead of being dropped, because a
# silently skipped country is weight that vanishes from the map unnoticed.
ISO2 = {
    "United States": "US", "Japan": "JP", "United Kingdom": "GB", "Canada": "CA",
    "Germany": "DE", "France": "FR", "Switzerland": "CH", "Netherlands": "NL",
    "Australia": "AU", "Ireland": "IE", "Sweden": "SE", "Denmark": "DK",
    "Italy": "IT", "Spain": "ES", "Finland": "FI", "Belgium": "BE",
    "Norway": "NO", "Austria": "AT", "Portugal": "PT", "Israel": "IL",
    "New Zealand": "NZ", "Singapore": "SG", "Hong Kong": "HK", "Luxembourg": "LU",
    "Poland": "PL", "Greece": "GR", "Czech Republic": "CZ", "Hungary": "HU",
    "Taiwan": "TW", "South Korea": "KR", "Korea": "KR", "China": "CN",
    "India": "IN", "Brazil": "BR", "South Africa": "ZA", "Saudi Arabia": "SA",
    "Mexico": "MX", "Malaysia": "MY", "Thailand": "TH", "Indonesia": "ID",
    "Turkey": "TR", "Philippines": "PH", "Chile": "CL", "Qatar": "QA",
    "United Arab Emirates": "AE", "Kuwait": "KW", "Peru": "PE", "Colombia": "CO",
    "Egypt": "EG", "Russia": "RU", "Argentina": "AR", "Vietnam": "VN",
    "Pakistan": "PK", "Romania": "RO", "Kazakhstan": "KZ", "Cyprus": "CY",
    "Bermuda": "BM", "Cayman Islands": "KY", "Jersey": "JE", "Guernsey": "GG",
    "Macau": "MO", "Uruguay": "UY", "Iceland": "IS", "Slovenia": "SI",
    "Slovakia": "SK", "Estonia": "EE", "Latvia": "LV", "Lithuania": "LT",
    "Malta": "MT", "Croatia": "HR", "Bulgaria": "BG",
}

OTHER = "Other"                       # justETF's aggregated remainder row

_NAME_RE = re.compile(r'countries_value_name">([^<]+)</td>')
_PCT_RE = re.compile(r'countries_value_percentage">([\d.,]+)%')
_MORE_RE = re.compile(r'"u":"(/en/etf-profile\.html\?[^"]*loadMoreCountries[^"]*)"')
_TITLE_RE = re.compile(r"<title>([^<|]+)")


class SeedError(RuntimeError):
    """This instrument produced something we refuse to write."""


# ── discovery ─────────────────────────────────────────────────────────────
def holdings(db: str = CARTERA_DB) -> list[dict]:
    """Open positions in the portfolio: [{ticker, name}]. Empty if no database.

    Sold-out positions are excluded — seeding geography for something no longer
    held is a request to someone else's server for nothing.
    """
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("""
            SELECT ticker,
                   COALESCE(MAX(name), ''),
                   SUM(CASE WHEN side='buy' THEN quantity ELSE -quantity END)
            FROM movements GROUP BY ticker""").fetchall()
    finally:
        con.close()
    return [{"ticker": t, "name": n or t} for t, n, q in rows if (q or 0) > 1e-9]


def resolve(ticker: str, name: str) -> dict:
    """How this ticker's geography can be fetched, or why it cannot be."""
    manual = PROXIES.get(ticker)
    if manual:
        # `is_proxy` marks a stand-in for something else, which the UI must
        # disclose. Gold carries no ISIN at all, so it is not one.
        return {"ticker": ticker, "name": name, **manual,
                "needs_proxy": False, "is_proxy": bool(manual["proxy_isin"])}
    m = ISIN_RE.search(ticker.upper())
    if m:
        # A listed ETF: justETF profiles it under its own ISIN, so this is the
        # instrument itself, not a stand-in, and there is nothing to disclose.
        return {"ticker": ticker, "name": name, "asset_class": DEFAULT_CLASS,
                "proxy_isin": m.group(1), "proxy_name": None,
                "proxy_note": None, "needs_proxy": False, "is_proxy": False}
    return {"ticker": ticker, "name": name, "asset_class": DEFAULT_CLASS,
            "proxy_isin": None, "proxy_name": None, "needs_proxy": True,
            "is_proxy": False,
            "proxy_note": "hace falta elegir un ETF de referencia del mismo índice"}


# ── scraping ──────────────────────────────────────────────────────────────
def _opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def _get(op, url: str, extra: dict | None = None) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **(extra or {})})
    with op.open(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "ignore")


def _rows(html: str) -> list[tuple[str, float]]:
    names, pcts = _NAME_RE.findall(html), _PCT_RE.findall(html)
    if len(names) != len(pcts):
        raise SeedError(f"{len(names)} country names vs {len(pcts)} percentages")
    return [(n.strip(), float(p.replace(",", "."))) for n, p in zip(names, pcts)]


def fetch_countries(isin: str) -> tuple[list[tuple[str, float]], str]:
    """(top-N countries + `Other`, fund title) for an ISIN.

    The profile page renders four countries; a Wicket link expands it to ten.
    That link is a TOGGLE, so it is followed exactly once — following it twice
    collapses the table back to four and silently halves the coverage.
    """
    op = _opener()
    base = f"https://www.justetf.com/en/etf-profile.html?isin={isin}"
    page = _get(op, base)
    rows = _rows(page)
    if not rows:
        raise SeedError("sin tabla de países en la ficha")
    title = (_TITLE_RE.search(page) or [None, ""])[1].strip()

    m = _MORE_RE.search(page)
    if m:
        expanded = _get(
            op, "https://www.justetf.com" + m.group(1).replace("&amp;", "&"),
            {"Wicket-Ajax": "true",
             "Wicket-Ajax-BaseURL": f"en/etf-profile.html?isin={isin}",
             "X-Requested-With": "XMLHttpRequest", "Referer": base})
        more = _rows(expanded)
        # Only accept the expansion when it really is more. If justETF ever
        # returns the collapsed table here, keeping the smaller list is right —
        # silently trading ten countries for four would not be visible anywhere.
        if len(more) > len(rows):
            rows = more
    return rows, title


def to_weights(rows: list[tuple[str, float]]) -> tuple[dict, float]:
    """(ISO2 -> percent, other_percent). Unknown country names fail loudly."""
    weights, other, unknown = {}, 0.0, []
    for name, pct in rows:
        if name == OTHER:
            other += pct
            continue
        code = ISO2.get(name)
        if not code:
            unknown.append(name)
            continue
        weights[code] = round(weights.get(code, 0.0) + pct, 4)
    if unknown:
        raise SeedError(f"países fuera de la tabla ISO2: {unknown}. "
                        "Añádelos en vez de dejar que su peso desaparezca.")
    total = sum(weights.values()) + other
    if not 95.0 <= total <= 105.0:
        raise SeedError(f"los pesos suman {total:.2f}%")
    return weights, round(other, 4)


# ── the run ───────────────────────────────────────────────────────────────
def load_existing() -> dict:
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"instruments": {}}


def seed(only: str | None = None, quiet: bool = False) -> int:
    say = (lambda *a: None) if quiet else print
    today = dt.date.today().isoformat()
    prev = load_existing()
    out = {"as_of": today,
           "note": ("Pesos por país con transparencia (look-through). Fuente justETF, "
                    "top-10 países más un resto agregado que NO se reparte."),
           "instruments": dict(prev.get("instruments") or {})}

    held = holdings()
    if not held:
        say("Sin posiciones abiertas: nada que sembrar.")
        return 0
    targets = [h for h in held if only is None or h["ticker"] == only]
    if only and not targets:
        print(f"{only} no es una posición abierta.", file=sys.stderr)
        return 1

    # A holding that has been sold is left in the file rather than deleted: the
    # table is cheap, and a re-purchase should not have to wait for a scrape.

    fresh, failed = 0, []
    for i, h in enumerate(targets):
        spec = resolve(h["ticker"], h["name"])
        tk = spec["ticker"]
        common = {"name": h["name"] or tk, "asset_class": spec["asset_class"],
                  "as_of": today}

        if spec["needs_proxy"]:
            # Recorded, not skipped. The API surfaces it so the page can say
            # "this needs a reference chosen" instead of the position quietly
            # missing from a map that otherwise looks complete.
            out["instruments"][tk] = {
                **common, "weights": {}, "other": 0.0, "no_geography": False,
                "needs_proxy": True, "proxy": None, "source": None,
                "note": spec["proxy_note"]}
            say(f"  {tk:18} sin referencia — hace falta elegir un ETF del mismo índice")
            continue

        if not spec["proxy_isin"]:
            out["instruments"][tk] = {
                **common, "weights": {}, "other": 0.0, "no_geography": True,
                "needs_proxy": False, "proxy": None, "source": None,
                "note": spec["proxy_note"]}
            say(f"  {tk:18} sin geografía ({h['name']})")
            continue

        try:
            rows, title = fetch_countries(spec["proxy_isin"])
            weights, other = to_weights(rows)
        except (SeedError, OSError) as e:
            # One bad fund must not cost the whole refresh. The previous entry
            # for this instrument stays exactly as it was — stale and honestly
            # dated beats absent.
            failed.append((tk, str(e)))
            say(f"  {tk:18} FALLO, se conserva lo anterior: {e}")
            continue

        out["instruments"][tk] = {
            **common, "weights": weights, "other": other, "no_geography": False,
            "needs_proxy": False,
            "proxy": ({"isin": spec["proxy_isin"],
                       "name": spec["proxy_name"] or title,
                       "note": spec["proxy_note"]} if spec["is_proxy"] else None),
            "source": f"https://www.justetf.com/en/etf-profile.html?isin={spec['proxy_isin']}"}
        fresh += 1
        say(f"  {tk:18} {len(weights)} países, otros {other:.2f}% ({h['name']})")
        if i < len(targets) - 1:
            time.sleep(1.5)            # be a polite guest on someone's server

    # `as_of` is the file's freshness. If nothing was refreshed it must NOT
    # advance, or a run where every fetch failed would look like a fresh table.
    if not fresh:
        out["as_of"] = prev.get("as_of") or today

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, OUT)               # atomic: the dashboard never sees a half file
    say(f"\n{fresh} actualizados -> {OUT}")
    if failed:
        print(f"{len(failed)} fallidos: {failed}", file=sys.stderr)
    return 0 if fresh or not failed else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Siembra la tabla de países de la cartera.")
    ap.add_argument("--only", metavar="TICKER", help="sembrar un solo instrumento")
    ap.add_argument("--quiet", action="store_true", help="sin salida en caso de éxito")
    a = ap.parse_args()
    return seed(only=a.only, quiet=a.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
