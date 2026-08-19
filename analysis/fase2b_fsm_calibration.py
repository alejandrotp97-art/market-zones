#!/usr/bin/env python3
"""FASE 2 (calibration) — does v2 label the stress windows better than v1?

Metric: macro-concordance. Bull windows should read risk-ON, bear/crisis windows
risk-OFF. Measured across 8 assets, v1 vs v2, with no per-window tuning — the v2
change is structural (acute-spike gate on shocks, calm gate on Lateral).
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/alex/bots/market-zones")
from regime import analyze
from regime import regimes as R
from regime.regimes import classify_series
from zones import fetch_daily

ASSETS = ["SPY","QQQ","^RUT","^GDAXI","^N225","GLD","TLT","BTC-USD"]
RISK_ON = {R.ALCISTA, R.SOBRECAL, R.RECUPERACION}
RISK_OFF = {R.PANICO, R.CAPITULACION, R.CORRECCION, R.DISTRIBUCION}
BULL = [(2013,2013),(2017,2017),(2019,2019),(2023,2024)]
BEAR = [(2000,2002),(2008,2008),(2018,2018),(2022,2022)]

def regimes_both(frame):
    args = (frame["score"].to_numpy(), frame["vol_p"].to_numpy(),
            frame["instab_p"].to_numpy(), frame["trend_up"].to_numpy())
    return classify_series(*args, calibrated=False), classify_series(*args, calibrated=True)

conc = {"v1": {"bull": [], "bear": []}, "v2": {"bull": [], "bear": []}}
panic2022 = {"v1": [], "v2": []}; lateral2008 = {"v1": [], "v2": []}
for a in ASSETS:
    try: df = fetch_daily(a, years=25)
    except Exception as e: print("skip", a, e); continue
    frame, _ = analyze(df)
    r1, r2 = regimes_both(frame)
    f = frame.copy(); f["y"] = pd.to_datetime(f["date"]).dt.year; f["r1"] = r1; f["r2"] = r2
    f = f.dropna(subset=["score"])
    for tag, wins, expect in [("bull", BULL, RISK_ON), ("bear", BEAR, RISK_OFF)]:
        for (y0, y1) in wins:
            g = f[(f.y >= y0) & (f.y <= y1)]
            if len(g) < 100: continue
            conc["v1"][tag].append(g["r1"].isin(expect).mean())
            conc["v2"][tag].append(g["r2"].isin(expect).mean())
    g22 = f[f.y == 2022]
    if len(g22) > 100:
        panic2022["v1"].append((g22["r1"] == R.PANICO).mean()); panic2022["v2"].append((g22["r2"] == R.PANICO).mean())
    g08 = f[f.y == 2008]
    if len(g08) > 100:
        lateral2008["v1"].append((g08["r1"] == R.LATERAL).mean()); lateral2008["v2"].append((g08["r2"] == R.LATERAL).mean())

print("=======  CONCORDANCIA MACRO (risk-on en bull / risk-off en bear)  =======")
print(f"{'':6s} {'bull→on':>9s} {'bear→off':>9s} {'media':>7s}")
for v in ("v1", "v2"):
    b = np.mean(conc[v]["bull"]); r = np.mean(conc[v]["bear"])
    print(f"  {v:4s} {b*100:8.1f}% {r*100:8.1f}% {(b+r)/2*100:6.1f}%")
print("\n=======  CASOS PROBLEMA (media 8 activos)  =======")
print(f"  Pánico en 2022 (bear lento):  v1 {np.mean(panic2022['v1'])*100:4.0f}%  →  v2 {np.mean(panic2022['v2'])*100:4.0f}%   (debe BAJAR)")
print(f"  Lateral en 2008 (crash):      v1 {np.mean(lateral2008['v1'])*100:4.0f}%  →  v2 {np.mean(lateral2008['v2'])*100:4.0f}%   (debe BAJAR)")

# SPY v2 stress distribution (compare to the v1 numbers from the prior run)
df = fetch_daily("SPY", years=25); frame, _ = analyze(df)
frame["y"] = pd.to_datetime(frame["date"]).dt.year
print("\n=======  SPY · distribución de régimen v2 por ventana  =======")
for lbl,(a,b) in {"2008 GFC":(2008,2009),"2020 COVID":(2020,2020),"2017 bull":(2017,2017),"2022 bear":(2022,2022)}.items():
    g = frame[(frame.y>=a)&(frame.y<=b)].dropna(subset=["regime"])
    vc = g["regime"].value_counts(normalize=True).mul(100).round(0)
    print(f"  {lbl:11s} score {g['score'].mean():3.0f} | " + ", ".join(f"{k} {int(v)}%" for k,v in vc.head(3).items()))
