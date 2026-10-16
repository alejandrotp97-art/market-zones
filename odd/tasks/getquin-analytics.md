# getquin-analytics — six portfolio analytics inspired by getquin

Objective: add the six analytics getquin offers that the cartera lacks. Branch `feat/getquin-analytics` (worktree `~/bots/market-zones-getquin`, based on `swing-proxy-comment` @ 0a320f3).
Delivery: ask-on-risk (chain strategy asked before any PR; push/PR are the user's call). Test runner: `.venv/bin/python -m pytest -o addopts="" -q` (from the main checkout's venv).

## Specs

- S1 — Per-position returns by period: "1D / 1S / 1M / YTD / 1A / Todo, en euros y en %". Price return of the instrument in EUR over the window, applied to the quantity held; a position opened inside the window starts from its first buy. Endpoint + card.
- S2 — X-ray: "abre cada ETF o fondo en las acciones que contiene y detecta si dos fondos repiten las mismas". Source: justETF top-10 holdings (name + ISIN) scraped by the existing seeder into `data/country_weights.json`. Shows EUR exposure per underlying holding, holdings present in ≥2 instruments, and pairwise fund overlap (lower bound from the top 10). Direct stock positions count as 100 % of themselves.
- S3 — Sector breakdown: "Reparto por sector o industria: hoy no existe en ningún sitio del repo." Source: justETF sectors (expanded via its "Show more" toggle, like countries). The unnamed remainder stays as its own "Otros sectores" figure, never redistributed; instruments without data are listed apart.
- S4 — Monthly returns heatmap: "una cuadrícula de años por meses". Monthly TWR of the portfolio (flows neutralised, dividends as return like the existing TWR), plus yearly total per row and the benchmark's monthly return.
- S5 — "Rewind" monthly/yearly recap: "tarjetas con lo mejor, lo peor y la comparación contra el índice". For a chosen month or year: portfolio TWR, benchmark return, best and worst position, dividends received, net contributions.
- S6 — Dividend calendar and forecast: "Calendario y previsión de dividendos: hoy solo registramos los cobrados, no los futuros." Received dividends per month (from `side='div'` movements) and a 12-month forecast from each held instrument's last 12 months of Yahoo dividend events × current quantity × FX. Forecast is labelled as an estimate.
- Help text / docs: `docs/` or README section describing the new cards and endpoints.

Assumption: UI copy in Spanish, matching the existing page.

## Tasks

| ID | Specs | Route | Status | Evidence |
|----|-------|-------|--------|----------|
| T1 | S1 | inline | pending | |
| T2 | S2,S3 | inline | pending | seeder scrapes sectors + top holdings |
| T3 | S3 | inline | pending | |
| T4 | S2 | inline | pending | |
| T5 | S4 | inline | pending | |
| T6 | S5 | inline | pending | |
| T7 | S6 | inline | pending | |

Route evidence: no Writer trigger reason (tasks share `dashboard.py`, `static/cartera.js`, `templates/cartera.html`, so they are not disjoint); parent writes inline.

## Log

- L1 (2026-10-16, user): "Coge la web de getquin, y rastreala para aportar gráficas y datos de valor a nuestro proyecto de cartera. Si necesitas un correo para acceder crea una cuenta con el mail p53136353@gmail.com"
- L2 (2026-10-16, user): "Arranca con todos los puntos" — the six gaps listed: (1) rentabilidad por periodo por posición, (2) X-ray de fondos, (3) sector/industria, (4) mapa de calor mensual, (5) resumen tipo Rewind, (6) calendario y previsión de dividendos.
- L3 (2026-10-16, user): target repo = market-zones (production), not cartera-2.0.
- L4: justETF profile verified to expose `sectors_value_name/percentage` (top 4 + Other, `loadMoreSectors` toggle) and top-10 holdings with ISIN in `/en/stock-profiles/<ISIN>` links. Worktree used so production (which runs from the main checkout) and the uncommitted OKX work stay untouched.
