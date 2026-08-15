#!/usr/bin/env python3
"""Run the market-zone index on a Yahoo ticker and print the current read.

    python cli.py NLR
    python cli.py URNM --years 15 --tail 8
"""
from __future__ import annotations

import argparse
import sys

from zones import BadSymbol, NoHistory, analyze, fetch_daily

BAR = "─" * 60


def main() -> None:
    ap = argparse.ArgumentParser(description="Market-zone index for a Yahoo ticker.")
    ap.add_argument("symbol", help="Yahoo symbol, e.g. NLR, URNM, SPCX")
    ap.add_argument("--years", type=int, default=25, help="history lookback (years)")
    ap.add_argument("--since", default=None,
                    help="normalize only from this date, YYYY-MM-DD (in-sample window)")
    ap.add_argument("--tail", type=int, default=6, help="rows of recent history to print")
    args = ap.parse_args()

    try:
        df = fetch_daily(args.symbol, years=args.years)
    except (NoHistory, BadSymbol) as e:
        # Both are the caller's problem, not a crash: a symbol that is not a
        # ticker shape, or one Yahoo prices but will not chart.
        print(f"{BAR}\n {e}\n{BAR}", file=sys.stderr)
        raise SystemExit(2)
    if args.since:
        df = df[df["date"] >= args.since].reset_index(drop=True)
    frame, s = analyze(df)

    print(BAR)
    print(f" {args.symbol}   ({len(df)} barras · modelo: {s.model})")
    print(BAR)
    print(f" Fecha        {s.date.date()}")
    print(f" Cierre       {s.close:,.2f}")
    print(f" Score        {s.score:5.1f}   (raw {s.score_raw:5.1f})")
    zone = s.zone_name or "—"
    print(f" Zona         {zone}   (hace {s.dwell} días)")
    print(BAR)
    print(" Componentes (0-100, alto=caro):")
    print(f"   Stretch    {_fmt(s.stretch)}")
    print(f"   RSI(14)    {_fmt(s.rsi)}")
    print(f"   Drawdown   {_fmt(s.drawdown)}")
    print(f"   TrendDev   {_fmt(s.trend_dev)}")
    print(f"   Volatilidad{_fmt(s.volatility)}")
    conv = s.conviction_label or "—"
    print(f" Convicción   {conv}   (clímax {_fmt(s.climax)})")
    print(BAR)
    print(f" {s.verdict}")
    print(BAR)

    cols = ["date", "close", "stretch", "rsi", "drawdown", "trend_dev",
            "volatility", "score", "zone_name"]
    print(frame[cols].tail(args.tail).to_string(index=False))


def _fmt(x: float) -> str:
    return f"{x:5.1f}" if x == x else "  n/a"  # x!=x -> NaN


if __name__ == "__main__":
    main()
