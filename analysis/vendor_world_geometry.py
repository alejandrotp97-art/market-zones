#!/usr/bin/env python3
"""Build `static/world-110m.json` — the map outlines the Cartera page draws.

Run BY HAND when the geometry needs refreshing:

    python3 analysis/vendor_world_geometry.py

Source: Natural Earth 1:110m Admin-0 countries, **public domain** (no attribution
required, though it is credited in the UI anyway). Vendored rather than fetched
at runtime: the dashboard must render with no outbound call, and a map that
depends on a third party being up is a map that is sometimes blank.

WHAT IS STRIPPED
Everything except an ISO alpha-2 code and the outline. Natural Earth ships ~90
attributes per country — names in a dozen languages, population, economy tier —
none of which this page reads. Coordinates are rounded to two decimals (~1 km),
far finer than a world map at this resolution can show, and consecutive points
that collapse onto each other after rounding are dropped.

WHAT IS ABSENT, ON PURPOSE
At 1:110m several small holders of real weight have no polygon at all — Hong
Kong, Singapore, Jersey, Macau, Malta. That is a property of the source, not a
bug to paper over. The file records which ISO codes it DOES carry so the page
can say plainly that a country is held but not drawable, instead of leaving the
weight looking like zero.
"""
from __future__ import annotations

import gzip
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# `static/`, not `vendor/`: this is a browser asset served over HTTP, while
# vendor/ holds vendored Python packages and is on the service's PYTHONPATH.
OUT = os.path.join(os.path.dirname(HERE), "static", "world-110m.json")

URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
       "geojson/ne_110m_admin_0_countries.geojson")

PRECISION = 2                      # decimals of degree kept (~1.1 km at equator)

# Natural Earth writes "-99" for ISO_A2 on a handful of sovereign states
# (a disputed-code convention). Filling these by name is safer than trusting
# ISO_A2_EH alone, which varies between releases of the file.
BY_NAME = {
    "France": "FR", "Norway": "NO", "Somaliland": "SO", "Kosovo": "XK",
    "N. Cyprus": "CY", "Northern Cyprus": "CY",
}

SKIP = {"AQ"}                      # Antarctica: a third of the height, never held


def _iso2(props: dict) -> str | None:
    for key in ("ISO_A2_EH", "ISO_A2", "WB_A2"):
        v = (props.get(key) or "").strip().upper()
        if v and v not in ("-99", "NA"):      # "NA" is Namibia's code read as null
            return v
    if (props.get("ISO_A2") or "").strip() == "NA":
        return "NA"
    for key in ("NAME", "ADMIN", "NAME_LONG"):
        hit = BY_NAME.get((props.get(key) or "").strip())
        if hit:
            return hit
    return None


def _ring(ring):
    """Round a ring and drop points that rounding made identical."""
    out = []
    for x, y in ring:
        p = [round(x, PRECISION), round(y, PRECISION)]
        if not out or out[-1] != p:
            out.append(p)
    # A ring needs 4 points to close; anything less is a speck, not an outline.
    return out if len(out) >= 4 else None


def _polys(geom):
    """Geometry -> list of polygons, each a list of rings. Holes are kept."""
    t, coords = geom.get("type"), geom.get("coordinates") or []
    raw = [coords] if t == "Polygon" else coords if t == "MultiPolygon" else []
    out = []
    for poly in raw:
        rings = [r for r in (_ring(ring) for ring in poly) if r]
        if rings:
            out.append(rings)
    return out


def main() -> int:
    print(f"Descargando {URL}")
    req = urllib.request.Request(URL, headers={"User-Agent": "market-zones/1.0",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    src = json.loads(raw.decode("utf-8"))

    shapes: dict[str, list] = {}
    dropped = []
    for feat in src.get("features", []):
        props = feat.get("properties") or {}
        code = _iso2(props)
        if not code:
            dropped.append(props.get("NAME") or props.get("ADMIN") or "?")
            continue
        if code in SKIP:
            continue
        polys = _polys(feat.get("geometry") or {})
        if not polys:
            continue
        # Some states arrive as several features (mainland + territories).
        shapes.setdefault(code, []).extend(polys)

    out = {
        "source": "Natural Earth 1:110m Admin-0 countries (public domain)",
        "url": URL,
        "precision": PRECISION,
        "shapes": shapes,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")

    size = os.path.getsize(OUT)
    print(f"{len(shapes)} países  ·  {size/1024:.0f} KB  ->  {OUT}")
    if dropped:
        print(f"sin código ISO, descartados: {dropped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
