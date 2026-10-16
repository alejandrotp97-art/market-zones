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
| T1 | S1 | inline | done | c71c20b; RED 3 failed → GREEN 12 passed; suite 649 passed; assess high (process_boundary dashboard.py), review deferred to one slice review |
| T2 | S2,S3 | inline | done | 356ff45; RED 8 failed → GREEN; live smoke IE00B0M63177 (12 países, 14 sectores, top 10) |
| T3 | S3 | inline | done | cf92a9e (with T4); RED collection error → GREEN 11 passed |
| T4 | S2 | inline | done | cf92a9e |
| T5 | S4 | inline | done | 8628a14; RED → GREEN 6 passed; fixed `.ix` class clash caught by layout test |
| T6 | S5 | inline | done | eb7f5c1; RED 6 failed → GREEN 7 passed |
| T7 | S6 | inline | done | 8d6c35f; RED → GREEN 7 passed |
| T8 | docs | inline | done | 1d14934 docs/CARTERA.md + display polish (−0, Sovereign, 1D NAV note) |

Route evidence: no Writer trigger reason (tasks share `dashboard.py`, `static/cartera.js`, `templates/cartera.html`, so they are not disjoint); parent writes inline.

## Log

- L1 (2026-10-16, user): "Coge la web de getquin, y rastreala para aportar gráficas y datos de valor a nuestro proyecto de cartera. Si necesitas un correo para acceder crea una cuenta con el mail p53136353@gmail.com"
- L2 (2026-10-16, user): "Arranca con todos los puntos" — the six gaps listed: (1) rentabilidad por periodo por posición, (2) X-ray de fondos, (3) sector/industria, (4) mapa de calor mensual, (5) resumen tipo Rewind, (6) calendario y previsión de dividendos.
- L3 (2026-10-16, user): target repo = market-zones (production), not cartera-2.0.
- L4: justETF profile verified to expose `sectors_value_name/percentage` (top 4 + Other, `loadMoreSectors` toggle) and top-10 holdings with ISIN in `/en/stock-profiles/<ISIN>` links. Worktree used so production (which runs from the main checkout) and the uncommitted OKX work stay untouched.
- L5 (2026-10-16): all tasks green; full suite 695 passed; ruff clean except 4 pre-existing findings (test_catalogo, test_firds). Visual check on a copy of the real DB at :8799 from the worktree (headless Chromium, no console errors). Every commit assesses `high` (process_boundary signal in dashboard.py) → one slice review over 0a320f3..HEAD instead of per-commit consent. Known: data/country_weights.json in production needs one reseed for sectors/holdings; the pension-plan proxy lives in the uncommitted main-checkout change.
- L6 (2026-10-16): independent verifier: S1–S6 + docs PASS, no blockers; advisory 1 (network error on sector expansion aborted the fund) fixed in 6f77677. Native review over 0a320f3..6f77677 (lenses resilience, readability, reliability) granted by the user → approved and acknowledged (lineage review-8a4ca146e8439b46). Non-blocking follow-ups: R3/R4 a failed sector fetch overwrites previously seeded sectors/holdings with empty ones; R3 the "Cartera" total of /periodos omits positions fully sold inside the window; R4 dividend events degrade silently to "no forecast" when Yahoo omits them.
- L7 (2026-10-16, user): "sí, arregla las tres y luego mergea" → 4969140 fixes the three review follow-ups (RED 4 → GREEN, suite 700). Native review of 6f77677..4969140 granted → approved, acknowledged. Merged fast-forward into swing-proxy-comment; the uncommitted OKX work was re-applied on top (only conflict: one import line; changes verified identical to the saved patch; 755 tests on .venv 3.14 and on the production 3.12 venv). User chose "Mergear y reiniciar": restarted market-zones@nacho, market-zones@nico and market-zones.service (:8771, the owner's instance, same checkout); geo-seed run → sectors/holdings present; :8771 serves all six endpoints 200, page has no console errors. Open: nacho/nico answer 403 on every /api/cartera* because their env sets PUBLIC_HOST without CARTERA_BEHIND_AUTH=1 (pre-existing config, unchanged). Remaining non-blocking review notes: kept holdings carry no own date; forecast.unknown semantics.
- L8 (2026-10-16, user): "sí, añade CARTERA_BEHIND_AUTH a nacho y nico" → appended CARTERA_BEHIND_AUTH=1 to ~/etc/carteras/{nacho,nico}.env (outside the repo), restarted both; all seven /api/cartera* routes 200 on :8782/:8783; Caddy :8780 still answers 401 without or with wrong credentials; backends listen on 127.0.0.1 only.
