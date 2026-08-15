# Geographic exposure map — Cartera page

**Date:** 2026-08-06
**Status:** approved

## Problem

The `/cartera` page reports what the portfolio holds and what it is worth, but
not *where the money is*. The obvious implementation — colour a country by the
instrument's domicile — is worse than nothing here: four of the five holdings
are funds, so the map would paint Ireland and France at ~97% and describe the
paperwork rather than the portfolio.

A useful map needs **look-through**: each fund decomposed into the geographic
exposure of what it actually holds. No free API publishes that, so the weights
must come from a curated table.

## Holdings and their country source

| Position | Country weights come from | Note |
|---|---|---|
| Fidelity MSCI World Index Fund | iShares Core MSCI World (IE00B4L5Y983) | same index, different vehicle |
| Vanguard Em Mkts Stk Idx | iShares MSCI EM | same index |
| Vanguard 20+ Yr € Treasury Idx | iShares € Govt Bond 20yr+ | same segment |
| VanEck Uranium & Nuclear (IE000M7V94E1) | itself | already an ETF |
| Amundi Physical Gold ETC | — | bullion has no country |

A proxy is an approximation and is labelled as one. The proxy ISIN and the
as-of date travel with the data and are shown in the UI, so the map never
claims more precision than it has.

## Architecture

### Data — `data/country_weights.json`

Version-controlled, hand-curated, seeded by a script that is run manually:

```json
{
  "as_of": "2026-08-06",
  "instruments": {
    "0P0001CLDK.F": {
      "name": "Fidelity MSCI World Index Fund",
      "asset_class": "equity",
      "proxy": {"isin": "IE00B4L5Y983", "name": "iShares Core MSCI World"},
      "source": "https://www.justetf.com/en/etf-profile.html?isin=IE00B4L5Y983",
      "as_of": "2026-08-06",
      "weights": {"US": 68.37, "JP": 5.55, "...": 0.0}
    }
  }
}
```

`analysis/seed_country_weights.py` scrapes the `Countries` table justETF renders
server-side and writes this file. **The dashboard never scrapes.** It reads the
JSON. When justETF changes its markup the seeder breaks offline, at a moment
chosen by the operator — never in a request.

### Backend — `geo.py`

A new module, not more of `dashboard.py` (already 1514 lines).

```
country_exposure(positions, table, asset_class=None) -> dict
```

A pure function over the positions `_cartera_payload` already computes. Rules:

- Only positions with `valued == True` contribute. Same discipline as the
  existing summary: a partial numerator over a full denominator reads as a loss.
- `market_value` (EUR) x country weight, aggregated by ISO-3166-1 alpha-2.
- Anything without country weights — gold, an instrument missing from the
  table, a fund whose weights do not sum — goes to `unmapped` **with a reason**
  and is reported. It is never silently dropped and never diluted into the
  percentages.
- Percentages are of the mapped total, and the mapped total is published beside
  them so the gap is visible.

Endpoint: `GET /api/cartera/geo?clase=all|equity|bond`.

### Frontend — SVG

`static/world-110m.json`: Natural Earth 110m admin-0, public domain, reduced to
ISO_A2 + geometry with coordinates rounded — 174 countries, 141KB. Built by
`analysis/vendor_world_geometry.py`. It lives in `static/` rather than `vendor/`
because it is a browser asset; `vendor/` holds vendored Python packages and is
on the service's PYTHONPATH.

At this resolution some countries carrying real weight have no polygon at all —
Jersey (1.5% of the uranium ETF) and Hong Kong. They keep their place in the
ranked list and in every total, and the page names them as undrawable rather
than letting their weight read as zero.

`static/cartera-map.js` and `static/cartera-map.css`; the section lives below
"Posiciones actuales".

**SVG rather than canvas**, deliberately departing from the time-series charts
on this page. Hit-testing a country under the cursor on canvas needs
point-in-polygon or a colour-index buffer; in SVG, hover, focus and keyboard
navigation come for free. The Natural Earth projection is a closed-form
polynomial, about fifteen lines — no library.

Colour: single-hue sequential, **sqrt scale**. Linear would render one dark
blob on the US (~68% of the equity sleeve) and leave everything else
indistinguishable from empty. The legend carries real percentages, so the
compression aids reading without misstating the numbers. Theme handled by the
existing CSS variables.

Interaction:

- hover / focus a country: percentage, EUR, and which funds contribute it
- toggle: Todo / Renta variable / Renta fija — sovereign debt and equity are
  not the same exposure and must be separable
- a ranked list beside the map, because a choropleth cannot be read for rank.
  It scrolls; the coverage notes deliberately sit OUTSIDE that scroll, since
  they are where the page admits what is not painted (~20% of this portfolio,
  between the aggregated remainder and the gold) and a disclosure hidden behind
  a scrollbar is not a disclosure.

## Testing — `tests/test_geo.py`

- weights aggregate correctly across instruments
- asset-class filter partitions the total
- an unvalued position never contributes
- an instrument absent from the table lands in `unmapped` with a reason
- gold never paints a country
- percentages are of the mapped total, not the portfolio total

## Known limits

1. Proxy ETFs are not the held funds. Same index, so country weights track
   closely; the divergence is not measured.
2. Weights are a snapshot. The as-of date is displayed; refreshing is running
   the seeder.
3. justETF publishes country of listing, not revenue exposure. A US-listed
   company earning half its revenue in China counts as US.
