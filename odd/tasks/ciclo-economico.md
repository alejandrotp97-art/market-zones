# ciclo-economico — business-cycle phase chart inside the cartera

Objective: classify the current phase of the economic cycle (Auge / Recesión / Crisis / Recuperación) from macro data, financial conditions, market risk and news, and show it as a chart on `/cartera`. Branch `feat/ciclo-economico` (branched from `swing-proxy-comment`; the uncommitted OKX work in the main checkout is left untouched).
Delivery: ask-on-risk. Test runner: `.venv/bin/python -m pytest -o addopts="" -q`. Progress tree: `.venv/bin/python odd/arbol.py ciclo-economico`.

## Specs

- S1 — Four phases, one chart: "crear una gráfica que indique en que estado del ciclo econocimo estamos; Auge, recesion, crissis o recuperacion". Phase model = OECD business-cycle clock (CLI level vs 100 × 1-month momentum): above trend & rising → Auge; above trend & falling → Recesión (slowdown); below trend & falling → Crisis; below trend & rising → Recuperación. Market-stress overlays (credit, NFCI, VIX) can escalate the reading and are shown, never hidden.
- S2 — Inputs: "Tiene que analizarse en funcion de las noticias, datos macroeconomicos, situación de riesgo de los mercados."
- S3 — Sources: "Puedes usar OCDE — indicadores adelantados, FRED — macroeconomía de EE. UU., The Conference Board — LEI y CEI, CME FedWatch — política monetaria, Spreads de crédito y condiciones financieras y demás datos que creas relevantas." Verified 2026-10-18: FRED `fredgraph.csv` and OECD SDMX answer without API key; CME FedWatch has no free endpoint (blocked from this VPS) and Conference Board LEI/CEI is paywalled. Assumption: FedWatch is approximated from market rates (2Y Treasury − effective fed funds), LEI/CEI from the OECD CLI plus the public FRED coincident series (payrolls, industrial production, real income, real sales); each substitute is labelled in the UI.
- S4 — Placement: "lo vamos a meter dentro de la cartera". A card on `/cartera` backed by its own endpoint.
- S5 — Progress tree: "Crea primero un arbol en la terminal de la evolución del proyecto para que pueda ir viendo la evolución de la omplementación y actulizacion de los cambios". `odd/arbol.py` renders this document's task table as a tree with status.
- Docs: `docs/CARTERA.md` section for the card, its sources and its substitutes.

Assumption: UI copy in Spanish, matching the existing page. Not investment advice: the card says so.

## Tasks

| ID | Specs | Route | Status | Evidence |
|----|-------|-------|--------|----------|
| T0 | S5 | inline | done | `odd/arbol.py` + this document |
| T1 | S3 | inline | done | `macro/sources.py`: FRED CSV + OCDE CLI con caché en disco; 7 tests (escritos a la vez que el código, sin RED previo); smoke real 19 series FRED + CLI USA/G20 |
| T2 | S2,S3 | inline | done | `macro/pillars.py`: 7 pilares, 23 indicadores, sustitutos LEI/CEI/FedWatch etiquetados; RED (collection error) → GREEN 8 passed |
| T3 | S2 | inline | done | `macro/news.py` + pilar «noticias»: EPU diario y EMV (FRED, índices de prensa) + tono de 100 titulares Google News; RED (collection error) → GREEN 6 passed |
| T4 | S1 | inline | pending | Clasificador: reloj del ciclo OCDE + capas de estrés → fase, confianza e historial |
| T5 | S4 | inline | pending | API `/api/cartera/ciclo` + caché + timer de refresco |
| T6 | S1,S4 | inline | pending | Gráfica en `/cartera`: reloj con estela, línea de fase y barras por pilar |
| T7 | docs | inline | pending | `docs/CARTERA.md` + despliegue (:8771, @nacho, @nico) |

## Log

- L1 (2026-10-18, user, verbatim): "Vamos a hacer una actualización al proyecto de situación del mercado de cada activo y lo vamos a meter dentro de la cartera. Va a consitir en crear una gráfica que indique en que estado del ciclo econocimo estamos; Auge, recesion, crissis o recuperacion. Tiene que analizarse en funcion de las noticias, datos macroeconomicos, situación de riesgo de los mercados. Puedes usar OCDE — indicadores adelantados,  FRED — macroeconomía de EE. UU., The Conference Board — LEI y CEI, CME FedWatch — política monetaria, Spreads de crédito y condiciones financieras y demás datos que creas relevantas. Crea primero un arbol en la terminal de la evolución del proyecto para que pueda ir viendo la evolución de la omplementación y actulizacion de los cambios"
- L2 (2026-10-18): source probe — FRED CSV 200 (NFCI 2026-10-09 = −0.499), OECD SDMX 200, CME FedWatch no response, GDELT 429 (rate limited). Next: T1.
- L3 (2026-10-18): T1 smoke — FRED multi-id returns a ZIP (one series per request). ICE BofA OAS only last 3 y on FRED (2023-10→), SP500 10 y; long history stress relies on NFCI/STLFSI4/VIX. Latest: CLI USA 100.999 (2026-09), HY OAS 3.23, NFCI −0.499, VIX 16.46, 2Y 4.77 vs EFFR 3.88.
- L4 (2026-10-18): T2 live check — composite 2026-10-16 +0.43; 2008-11 −0.70; 2020-04 −0.75; 2021-06 +0.75; 2019-08 −0.04 (leading −0.84). Monthly max age raised 75→100 d: series dated day 1 and published ~45 d later were dropped.
- L5 (2026-10-18): GDELT rejected — answers «limit requests to one every 5 seconds» even to the first request from this VPS. News pillar uses FRED USEPUINDXD (daily, 149.2 on 2026-10-15) and EMVOVERALLEMV (22.0, 2026-09) plus Google News RSS headline tone (100 items, 15 carry cycle words, tone +0.06). Headlines are rendered as text only.
