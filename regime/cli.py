#!/usr/bin/env python3
"""Run the validated minimal regime engine on a Yahoo ticker.

    python -m regime.cli SPY
    python -m regime.cli BTC-USD --tail 8
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zones import fetch_daily            # reuse the parent data adapter
from regime import analyze
from regime.analogs import HORIZONS, conditional_stats

BAR = "─" * 58


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--years", type=int, default=25)
    ap.add_argument("--tail", type=int, default=6)
    args = ap.parse_args()

    df = fetch_daily(args.symbol, years=args.years)
    frame, r = analyze(df)

    print(BAR)
    print(f" {args.symbol}   {r.date.date()}   ({len(df)} sesiones)")
    print(BAR)
    print(f" Score        {r.score:5.1f}   (0=capitulación · 100=euforia)")
    print(f" Régimen      {r.regime or '—'}   (hace {r.dwell} días)")
    print(BAR)
    print(" Ejes (percentil expanding, 0–100):")
    print(f"   Nivel/Score   {r.level:5.1f}")
    print(f"   Volatilidad   {r.vol:5.1f}")
    print(f"   Ciclo (dd)    {r.cycle:5.1f}")
    print(f"   Inestabilidad {r.instability:5.1f}")
    print(f"   Tendencia     {'↑ alcista' if r.trend_up else '↓ bajista/plana'}")
    print(BAR)
    cols = ["date", "close", "mayer_p", "vol_p", "dd_p", "score", "regime"]
    print(frame[cols].dropna(subset=["score"]).tail(args.tail).to_string(index=False))

    # ── conditional history: "desde una situación como hoy, ¿qué pasó?" ──
    st = conditional_stats(frame, method="regime")
    print("\n" + BAR)
    conf = "BAJA CONFIANZA" if st["low_confidence"] else "ok"
    print(f" Retorno posterior desde «{st['regime']}»   (n análogos = {st['n_analogs']}, {conf})")
    print(BAR)
    print(f" {'horiz':>5s} {'mediana':>9s} {'[p10, p90]':>18s} {'IC95 mediana':>18s} {'ddown':>7s}")
    for name in HORIZONS:
        h = st["horizons"].get(name, {})
        if "median" not in h:
            continue
        print(f" {name:>5s} {h['median']*100:8.1f}% [{h['p10']*100:6.1f}%,{h['p90']*100:6.1f}%]"
              f"  [{h['ci_lo']*100:5.1f}%,{h['ci_hi']*100:5.1f}%] {h['median_dd']*100:6.1f}%")
    print(BAR)


if __name__ == "__main__":
    main()
