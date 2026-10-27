# Alex's portfolio (market-zones :8771): hardening + optimisation
Objective: close the vulnerabilities of Alex's instance (tunnel-only) and make it faster.
Branch: fix/alex-cartera-hardening · Delivery: ask-on-risk · Tests: `.venv/bin/pytest -q` (828 at start).
Scope note: nacho/nico instances run the same code but are ON STANDBY: only `market-zones.service` is restarted.

## Specs
- S1 Security headers on every response: CSP with a per-response nonce on the app's own pages (inline scripts in comite.html and screener.html carry it), `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin` (no-referrer would make POSTs send `Origin: null` and trip the CSRF gate). `/swing` (proxied) gets the full CSP: its one inline script receives the nonce. Third-party static `/abraham/*` keeps working with frame-ancestors/object-src/base-uri only.
- S2 `static/app.js` escapes every string it puts into `innerHTML` (only numbers and constants go in raw).
- S3 Access by SSH tunnel only: user `alex` removed from the carteras Caddy site (basicauth line and map entry); `PUBLIC_HOST` stays unset. Verbatim (user): "sí, vamos con el túnel".
- S4 Optimisation plan, executed (user: "crea y ejecuta un plan de optimización"):
  - S4a Expired payload cache entries are served immediately (stale-while-revalidate) and rebuilt in the background, instead of a 1-1.7 s Yahoo download inside the request after 600 s.
  - S4b HTML pages and static JS/CSS are sent gzip-compressed when the browser accepts it (the tunnel bypasses Caddy).
  - S4c `/swing` does not hold a waitress thread for 30 s when :8775 is slow.

## Tasks
- T1 S1 inline · done db803fa (11 tests RED→GREEN, suite 839)
- T2 S2 inline · done c6a6dcb (no JS test runner: node --check + esc probe; browser: target card renders, 0 CSP errors)
- T3 S3 inline · user configs done (~/etc carteras Caddyfiles, backups .bak-<ts>); /etc/caddy pending user sudo script caddy-close-alex.sh
- T4 S4a inline · done b8a655a (7 tests RED→GREEN) + 28970e0 (pre-existing test leak fixed)
- T5 S4b inline · done bd58a28 (6 tests)
- T6 S4c inline · done 51cd2f6 (1 test)

## Log
- L1 (2026-10-27, user): "arreglá también las menores. LAs carteras de NAcho y Nico dejalas en standby. LA de Alex que es la miá corrige las vulneralbilidades y crea y ejecuta un plan de optimización"
- L2 (2026-10-27, user): "sí, vamos con el túnel"
- L3 Baseline (explorer, curl on 127.0.0.1): cold /api/zones 1.27 s, /api/regime 1.72 s, /api/cartera/history 1.10 s, /swing 1.26 s; warm 5-30 ms. Static ~300 KB JS uncompressed; pages 6-25 KB uncompressed.
- L4 T3 incident: `systemctl --user reload carteras-proxy` ran `caddy reload`, which (admin off in that instance) sent the user config to the SYSTEM Caddy on :2019; it failed to bind :8780 and kept serving the old config, but its admin API stopped listening. ExecReload removed from carteras-proxy.service; system Caddy must be restarted (not reloaded) by the sudo script. carteras-proxy listens on *:8780 plain HTTP — firewall state unknown (needs root).
- L5 Live after restart of market-zones.service only (nacho/nico untouched): suite 853; gzip cartera.js 124→39 KB, /cartera 25→7.5 KB, /swing 96→25 KB; Chromium 8 pages, 0 CSP violations, 0 writes, target card renders. 3×502 on /api/regime for ISIN.SG funds = Yahoo has no daily history (pre-existing).
