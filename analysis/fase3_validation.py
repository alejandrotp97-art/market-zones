#!/usr/bin/env python3
"""FASE 3 — settle the two open ties + permanence guards, empirically.

  Part 1 · Cross-era stability: info of each indicator across 5-year blocks × 8
           assets -> mean ± std. Head-to-head ext_atr vs mayer (lower std wins).
  Part 2 · Incremental info: residualize the forward target on the base set
           {rvol, ext_atr, dd}; MI(candidate; residual) -> who adds beyond base.
           This is the test that decides whether trend_dev earns its slot.
  Part 3 · Parameter insensitivity: rank-corr of each indicator under perturbed
           windows (SMA 180/220, vol 15/25, crosses 100/140). Fragile = overfit.
"""
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/alex/bots/market-zones")
from zones import fetch_daily

ASSETS = ["SPY", "QQQ", "^RUT", "^GDAXI", "^N225", "GLD", "TLT", "BTC-USD"]
BINS = 10

def build(df, sma=200, volw=20, crossw=120, atrw=14):
    c = df["close"].astype(float).reset_index(drop=True)
    h = df["high"].astype(float).reset_index(drop=True)
    l = df["low"].astype(float).reset_index(drop=True)
    lr = np.log(c).diff()
    s200 = c.rolling(sma).mean(); c.rolling(50).mean()
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/atrw, adjust=False).mean()
    rv = lr.rolling(volw).std() * np.sqrt(252)
    d = c.diff(); rs = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean() / (-d).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    lc = np.log(c).to_numpy(); td = np.full(len(lc), np.nan)
    if len(lc) >= sma:
        from numpy.lib.stride_tricks import sliding_window_view
        W = sliding_window_view(lc, sma); t = np.arange(sma); tb = t.mean(); tv = ((t - tb) ** 2).sum()
        b = (W * (t - tb)).sum(1) / tv; a = W.mean(1) - b * tb
        td[sma-1:] = W[:, -1] - (a + b * (sma - 1))
    sign = np.sign((c - s200).fillna(0))
    cross = (sign.diff().abs() > 0).astype(float).rolling(crossw).sum()
    return pd.DataFrame({
        "ext_atr": (c - s200) / atr, "mayer": c / s200,
        "slope200": s200.pct_change(20) / rv, "rsi14": 100 - 100/(1+rs),
        "roc60": c.pct_change(60), "rvol": rv, "dd": c / c.cummax() - 1,
        "crosses": cross, "trend_dev": pd.Series(td),
    }), c

def _bin(x, b):
    q = np.quantile(x, np.linspace(0, 1, b + 1)); q[0] -= 1e-9; q[-1] += 1e-9
    return np.clip(np.digitize(x, q[1:-1]), 0, b - 1)
def mi(x, y, b=BINS):
    m = np.isfinite(x) & np.isfinite(y); x, y = x[m], y[m]
    if len(x) < 200: return np.nan
    xb, yb = _bin(x, b), _bin(y, b); c = np.zeros((b, b)); np.add.at(c, (xb, yb), 1)
    p = c / c.sum(); px = p.sum(1); py = p.sum(0); s = 0.0
    for i in range(b):
        for j in range(b):
            if p[i, j] > 0: s += p[i, j] * np.log2(p[i, j] / (px[i] * py[j]))
    return s
def H(y, b=BINS):
    y = y[np.isfinite(y)]; _, cnt = np.unique(_bin(y, b), return_counts=True)
    p = cnt / cnt.sum(); return -np.sum(p * np.log2(p))

NAMES = ["ext_atr","mayer","slope200","rsi14","roc60","rvol","dd","crosses","trend_dev"]

# ── load panel once ───────────────────────────────────────────────────────────
DATA = {}
for a in ASSETS:
    try: DATA[a] = fetch_daily(a, years=25)
    except Exception as e: print("skip", a, e)
    time.sleep(0.3)

# ── Part 1 · cross-era info stability ─────────────────────────────────────────
cells = {n: [] for n in NAMES}
for _a, df in DATA.items():
    ind, c = build(df)
    fret = (c.shift(-20)/c - 1); fvol = (np.log(c).diff().rolling(20).std()*np.sqrt(252)).shift(-20)
    yr = pd.to_datetime(df["date"]).dt.year.reset_index(drop=True)
    d = ind.copy(); d["_fret"] = fret; d["_fvol"] = fvol; d["_era"] = (yr // 5) * 5
    d = d.replace([np.inf,-np.inf], np.nan)
    for _era, g in d.groupby("_era"):
        g = g.dropna()
        if len(g) < 400: continue
        Hr, Hv = H(g["_fret"].to_numpy()), H(g["_fvol"].to_numpy())
        for n in NAMES:
            cells[n].append(mi(g[n].to_numpy(), g["_fret"].to_numpy())/Hr + mi(g[n].to_numpy(), g["_fvol"].to_numpy())/Hv)

print("\n===========  PART 1 · info por era (5a × 8 activos)  ===========")
print(f"{'indicador':11s} {'info_media':>10s} {'std':>7s} {'CV':>6s}   (CV bajo = estable)")
stats = {n: (np.nanmean(cells[n]), np.nanstd(cells[n])) for n in NAMES}
for n in sorted(NAMES, key=lambda k:-stats[k][0]):
    m, s = stats[n]; print(f"{n:11s} {m:10.3f} {s:7.3f} {s/max(m,1e-9):6.2f}")
me, se = stats["ext_atr"]; mm, sm = stats["mayer"]
print(f"\n  >> ext_atr vs mayer:  info {me:.3f}±{se:.3f} (CV {se/me:.2f})  vs  {mm:.3f}±{sm:.3f} (CV {sm/mm:.2f})")
print(f"     GANADOR estabilidad: {'ext_atr' if se/me < sm/mm else 'mayer'}")

# ── Part 2 · incremental info beyond base {rvol, ext_atr, dd} ──────────────────
BASE = ["rvol","ext_atr","dd"]; CAND = ["trend_dev","slope200","roc60","rsi14","crosses","mayer"]
Xrows, yr_rows, yv_rows = [], [], []
for _a, df in DATA.items():
    ind, c = build(df)
    fret = (c.shift(-20)/c - 1); fvol = (np.log(c).diff().rolling(20).std()*np.sqrt(252)).shift(-20)
    d = ind.copy(); d["_fret"]=fret; d["_fvol"]=fvol
    d = d.replace([np.inf,-np.inf], np.nan).dropna()
    z = (d[NAMES] - d[NAMES].mean())/d[NAMES].std()          # standardize within asset
    Xrows.append(z); yr_rows.append((d["_fret"]-d["_fret"].mean())/d["_fret"].std())
    yv_rows.append((d["_fvol"]-d["_fvol"].mean())/d["_fvol"].std())
Z = pd.concat(Xrows, ignore_index=True); yr = pd.concat(yr_rows, ignore_index=True); yv = pd.concat(yv_rows, ignore_index=True)

def resid(y, cols):
    A = np.column_stack([np.ones(len(Z))] + [Z[c].to_numpy() for c in cols])
    beta, *_ = np.linalg.lstsq(A, y.to_numpy(), rcond=None)
    return y.to_numpy() - A @ beta

rr, rv = resid(yr, BASE), resid(yv, BASE)
print("\n===========  PART 2 · info incremental sobre base {rvol,ext_atr,dd}  ===========")
print("  (MI del candidato con el RESIDUO del objetivo tras quitar la base; ~0 = no aporta)")
print(f"{'candidato':11s} {'MI→ret|base':>12s} {'MI→vol|base':>12s} {'suma':>7s}")
for n in CAND:
    a = mi(Z[n].to_numpy(), rr)/H(rr); b = mi(Z[n].to_numpy(), rv)/H(rv)
    print(f"{n:11s} {a:12.4f} {b:12.4f} {a+b:7.4f}")

# ── Part 3 · parameter insensitivity (rank-corr vs baseline) ──────────────────
print("\n===========  PART 3 · sensibilidad a parámetros (Spearman vs base)  ===========")
def rankcorr(x, y):
    m = np.isfinite(x)&np.isfinite(y); return pd.Series(x[m]).corr(pd.Series(y[m]), method="spearman")
rows = {}
for _a, df in DATA.items():
    base,_ = build(df)
    for tag, kw, cols in [("SMA 200→180", {"sma": 180}, ["ext_atr","mayer","trend_dev","crosses"]),
                          ("SMA 200→220", {"sma": 220}, ["ext_atr","mayer","trend_dev","crosses"]),
                          ("vol 20→15",   {"volw": 15}, ["rvol"]),
                          ("vol 20→25",   {"volw": 25}, ["rvol"]),
                          ("cross 120→100",{"crossw": 100}, ["crosses"]),
                          ("cross 120→140",{"crossw": 140}, ["crosses"])]:
        alt,_ = build(df, **kw)
        for col in cols:
            rows.setdefault((col, tag), []).append(rankcorr(base[col].to_numpy(), alt[col].to_numpy()))
seen = []
for (col, tag), v in rows.items():
    print(f"  {col:10s} {tag:16s} rank-corr = {np.nanmean(v):.3f}")
