#!/usr/bin/env python3
"""Bake a fully self-contained HTML snapshot of the panel for one symbol.

Reuses the exact same CSS + Canvas renderer as the Flask app, but inlines the
data (window.__ZONES__) and runs in static mode (asset controls disabled, range
zoom still works). Handy for sharing / quick viewing without the SSH tunnel.

    python snapshot.py NLR
    python snapshot.py NLR --since 2023-02-03 --out /tmp/nlr.html
"""
from __future__ import annotations

import argparse
import json
import os

from dashboard import _clean
from zones import analyze, fetch_daily

HERE = os.path.dirname(os.path.abspath(__file__))


def build_payload(symbol: str, years: int, since: str | None) -> dict:
    df = fetch_daily(symbol, years=years)
    if symbol.upper().endswith("=F"):
        df = df.drop(columns=["volume"], errors="ignore")
    if since:
        df = df[df["date"] >= since].reset_index(drop=True)
    frame, s = analyze(df)
    series = []
    for _, r in frame.iterrows():
        sc = _clean(r["score"])
        if sc is None:
            continue
        series.append({
            "t": int(r["date"].timestamp() * 1000), "close": _clean(r["close"]),
            "score": sc, "zone": r["zone_name"], "stretch": _clean(r["stretch"]),
            "rsi": _clean(r["rsi"]), "drawdown": _clean(r["drawdown"]),
            "trend_dev": _clean(r["trend_dev"]),
            "volatility": _clean(r["volatility"]), "climax": _clean(r["climax"]),
        })
    return {
        "symbol": symbol, "as_of": str(s.date.date()), "model": s.model,
        "series": series,
        "summary": {"zone": s.zone_name, "score": _clean(s.score),
                    "close": _clean(s.close), "dwell": s.dwell,
                    "verdict": s.verdict, "date": str(s.date.date()),
                    "stretch": _clean(s.stretch), "rsi": _clean(s.rsi),
                    "drawdown": _clean(s.drawdown), "trend_dev": _clean(s.trend_dev),
                    "volatility": _clean(s.volatility),
                    "climax": _clean(s.climax), "vol_pct": _clean(s.vol_pct),
                    "volu_pct": _clean(s.volu_pct)},
    }


def render(symbol: str, payload: dict) -> str:
    css = open(os.path.join(HERE, "static", "style.css")).read()
    js = open(os.path.join(HERE, "static", "app.js")).read()
    data = json.dumps(payload)
    ranges = "".join(
        f'<button data-range="{r}"{" class=active" if r=="all" else ""}>{lab}</button>'
        for r, lab in [("all", "Todo"), ("30", "30A"), ("10", "10A"),
                       ("4", "4A"), ("2", "2A"), ("1", "1A")])
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel de Zonas de Mercado · {symbol}</title>
<style>{css}</style></head><body>
<div class="wrap">
  <header class="top">
    <div class="titles"><h1>Panel de Zonas de Mercado</h1>
      <div class="sub">Datos hasta <span id="asof">—</span></div></div>
    <div class="controls">
      <label class="lbl">Activo</label>
      <select id="asset"><option>{symbol}</option></select>
      <input id="ticker" type="text" placeholder="otro ticker…">
      <button id="load">Cargar</button>
      <div class="ranges" id="ranges">{ranges}</div>
    </div>
  </header>
  <section class="chart-card">
    <div class="legend"><span class="leg-score">Score</span><span class="leg-price">Precio (log)</span></div>
    <div class="chart-host"><canvas id="chart"></canvas>
      <div id="tip" class="tip" hidden></div><div id="status" class="status" hidden></div></div>
  </section>
  <footer class="verdict-card"><h2>Qué dice el índice</h2>
    <div class="verdict-row"><span id="zone-dot" class="dot"></span>
      <span id="zone-name" class="zone-name">—</span></div>
    <p id="verdict" class="verdict-text">—</p></footer>
</div>
<script>window.__STATIC__=true;window.__ZONES__={data};</script>
<script>{js}</script>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--years", type=int, default=25)
    ap.add_argument("--since", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    payload = build_payload(args.symbol, args.years, args.since)
    html = render(args.symbol, payload)
    out = args.out or os.path.join(HERE, f"snapshot_{args.symbol}.html")
    with open(out, "w") as f:
        f.write(html)
    print(out)


if __name__ == "__main__":
    main()
