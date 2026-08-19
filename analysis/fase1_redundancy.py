#!/usr/bin/env python3
"""FASE 1 — empirical redundancy / information analysis of the candidate set.

For every candidate indicator, across a cross-asset panel, estimates:
  - Spearman correlation matrix (redundancy, linear-monotone)
  - VIF (multicollinearity)
  - functional redundancy R2 = 1 - 1/VIF (how well the rest predict it)
  - Mutual Information with the OTHER indicators (redundancy, non-linear)
  - Mutual Information / Information Gain vs FORWARD return and FORWARD vol
    (informativeness about the future regime), normalized by target entropy
  - a data-driven class: IMPRESCINDIBLE / ÚTIL / REDUNDANTE / ELIMINAR

No lookahead is *deployed* here — this is a characterization of the feature set;
out-of-sample stability is FASE 3.
"""
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/alex/bots/market-zones")
from zones import fetch_daily

ASSETS = ["SPY", "QQQ", "^RUT", "^GDAXI", "^N225", "GLD", "TLT", "BTC-USD"]
BINS = 10

# ── indicator construction (raw values; per asset) ───────────────────────────
def indicators(df):
    c = df["close"].astype(float).reset_index(drop=True)
    h = df["high"].astype(float).reset_index(drop=True)
    l = df["low"].astype(float).reset_index(drop=True)
    lr = np.log(c).diff()
    sma200 = c.rolling(200).mean()
    sma50 = c.rolling(50).mean()
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    rv = lr.rolling(20).std() * np.sqrt(252)
    semi = np.sqrt((lr.clip(upper=0) ** 2).rolling(20).mean()) * np.sqrt(252)
    # Wilder RSI
    d = c.diff(); g = d.clip(lower=0); ls = (-d).clip(lower=0)
    rs = g.ewm(alpha=1/14, adjust=False).mean() / ls.ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - 100/(1+rs)
    # rolling 200d log-linear detrend residual (vectorized)
    lc = np.log(c).to_numpy(); w = 200
    td = np.full(len(lc), np.nan)
    if len(lc) >= w:
        from numpy.lib.stride_tricks import sliding_window_view
        W = sliding_window_view(lc, w)
        t = np.arange(w); tb = t.mean(); tv = ((t - tb) ** 2).sum()
        b = (W * (t - tb)).sum(1) / tv
        a = W.mean(1) - b * tb
        td[w-1:] = W[:, -1] - (a + b * (w - 1))
    # SMA200 crosses in last 120d
    sign = np.sign((c - sma200).fillna(0))
    cross = (sign.diff().abs() > 0).astype(float).rolling(120).sum()

    return pd.DataFrame({
        "ext_atr":   (c - sma200) / atr,
        "mayer":     c / sma200,
        "slope200":  sma200.pct_change(20) / rv,
        "sma50_200": (sma50 - sma200) / sma200,
        "rsi14":     rsi,
        "roc60":     c.pct_change(60),
        "rvol":      rv,
        "semivol":   semi,
        "dd":        c / c.cummax() - 1,
        "crosses":   cross,
        "trend_dev": pd.Series(td),
    }), c

NAMES = ["ext_atr","mayer","slope200","sma50_200","rsi14","roc60","rvol","semivol","dd","crosses","trend_dev"]

# ── estimators ───────────────────────────────────────────────────────────────
def _bin(x, bins):
    q = np.quantile(x, np.linspace(0, 1, bins + 1)); q[0] -= 1e-9; q[-1] += 1e-9
    return np.clip(np.digitize(x, q[1:-1]), 0, bins - 1)

def mi(x, y, bins=BINS):
    m = np.isfinite(x) & np.isfinite(y); x, y = x[m], y[m]
    if len(x) < 200: return np.nan
    xb, yb = _bin(x, bins), _bin(y, bins)
    c = np.zeros((bins, bins)); np.add.at(c, (xb, yb), 1)
    p = c / c.sum(); px = p.sum(1); py = p.sum(0)
    s = 0.0
    for i in range(bins):
        for j in range(bins):
            if p[i, j] > 0: s += p[i, j] * np.log2(p[i, j] / (px[i] * py[j]))
    return s

def entropy_bins(y, bins=BINS):
    m = np.isfinite(y); y = y[m]
    yb = _bin(y, bins); _, cnt = np.unique(yb, return_counts=True)
    p = cnt / cnt.sum(); return -np.sum(p * np.log2(p))

def vif_matrix(X):  # X standardized ndarray (rows=obs, cols=features)
    n = X.shape[1]; out = np.full(n, np.nan)
    for i in range(n):
        y = X[:, i]; A = np.delete(X, i, 1)
        A1 = np.column_stack([np.ones(len(A)), A])
        beta, *_ = np.linalg.lstsq(A1, y, rcond=None)
        r2 = 1 - np.sum((y - A1 @ beta) ** 2) / np.sum((y - y.mean()) ** 2)
        out[i] = 1 / max(1e-6, 1 - r2)
    return out

# ── run panel ────────────────────────────────────────────────────────────────
corrs, mis_pair, vifs, mi_ret, mi_vol = [], [], [], [], []
for a in ASSETS:
    try:
        df = fetch_daily(a, years=25)
    except Exception as e:
        print("skip", a, e); continue
    ind, c = indicators(df)
    fwd_ret = (c.shift(-20) / c - 1)
    lr = np.log(c).diff(); fwd_vol = (lr.rolling(20).std() * np.sqrt(252)).shift(-20)
    d = ind.copy(); d["_fret"] = fwd_ret; d["_fvol"] = fwd_vol
    d = d.replace([np.inf, -np.inf], np.nan).dropna()
    if len(d) < 500:
        print("short", a, len(d)); continue
    corrs.append(d[NAMES].corr(method="spearman"))
    Xs = ((d[NAMES] - d[NAMES].mean()) / d[NAMES].std()).to_numpy()
    vifs.append(vif_matrix(Xs))
    # pairwise MI matrix + forward MI
    M = np.full((len(NAMES), len(NAMES)), np.nan)
    for i in range(len(NAMES)):
        for j in range(i+1, len(NAMES)):
            M[i, j] = M[j, i] = mi(d[NAMES[i]].to_numpy(), d[NAMES[j]].to_numpy())
    mis_pair.append(M)
    Hr, Hv = entropy_bins(d["_fret"].to_numpy()), entropy_bins(d["_fvol"].to_numpy())
    mi_ret.append([mi(d[n].to_numpy(), d["_fret"].to_numpy())/Hr for n in NAMES])
    mi_vol.append([mi(d[n].to_numpy(), d["_fvol"].to_numpy())/Hv for n in NAMES])
    print(f"  {a:8s} n={len(d):5d}  ok")
    time.sleep(0.4)

C = sum(corrs)/len(corrs)
VIF = np.nanmean(np.vstack(vifs), 0)
MIP = np.nanmean(np.dstack(mis_pair), 2)
MIR = np.nanmean(np.vstack(mi_ret), 0)   # normalized MI vs fwd return
MIV = np.nanmean(np.vstack(mi_vol), 0)   # normalized MI vs fwd vol
maxcorr = np.array([np.max(np.abs(C.to_numpy()[i][np.arange(len(NAMES)) != i])) for i in range(len(NAMES))])
mi_others = np.array([np.nanmean(MIP[i][np.arange(len(NAMES)) != i]) for i in range(len(NAMES))])
func_red = 1 - 1/np.maximum(VIF, 1e-6)

# ── classification (data-driven) ─────────────────────────────────────────────
# redundancy clusters via union-find on |corr| > 0.80
parent = list(range(len(NAMES)))
def find(x):
    while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
    return x
for i in range(len(NAMES)):
    for j in range(i+1, len(NAMES)):
        if abs(C.to_numpy()[i, j]) > 0.80: parent[find(i)] = find(j)
clusters = {}
for i in range(len(NAMES)): clusters.setdefault(find(i), []).append(i)

info = MIR + MIV                       # composite informativeness (return + vol)
FLOOR = 0.010                          # below this: essentially no forward info
reps = {}
for root, members in clusters.items():
    reps[root] = max(members, key=lambda k: info[k])
rep_ids = sorted(reps.values(), key=lambda k: -info[k])
n_imp = max(1, (len(rep_ids)+1)//2)
imp_set = set(rep_ids[:n_imp])

cls = {}
for i in range(len(NAMES)):
    is_rep = (i == reps[find(i)])
    if not is_rep:
        cls[i] = "REDUNDANTE"
    elif info[i] < FLOOR:
        cls[i] = "ELIMINAR"
    elif i in imp_set:
        cls[i] = "IMPRESCINDIBLE"
    else:
        cls[i] = "ÚTIL"

# ── report ───────────────────────────────────────────────────────────────────
pd.set_option("display.width", 160)
print("\n================  CORRELACIÓN SPEARMAN (media 8 activos)  ================")
print(C.round(2).to_string())
print("\n================  CLASIFICACIÓN POR INDICADOR  ================")
print(f"{'indicador':11s} {'VIF':>6s} {'redFunc':>7s} {'max|r|':>6s} {'MI_otros':>8s} "
      f"{'MI→ret':>7s} {'MI→vol':>7s} {'info':>6s}  clase")
order = sorted(range(len(NAMES)), key=lambda k: (-{'IMPRESCINDIBLE':3,'ÚTIL':2,'REDUNDANTE':1,'ELIMINAR':0}[cls[k]], -info[k]))
for i in order:
    print(f"{NAMES[i]:11s} {VIF[i]:6.1f} {func_red[i]:7.2f} {maxcorr[i]:6.2f} {mi_others[i]:8.3f} "
          f"{MIR[i]:7.3f} {MIV[i]:7.3f} {info[i]:6.3f}  {cls[i]}")

print("\n================  CLUSTERS DE REDUNDANCIA (|r|>0.80)  ================")
for root, members in clusters.items():
    tag = " ".join(f"{NAMES[m]}{'*' if m==reps[root] else ''}" for m in members)
    print(f"  [{tag}]   (* = representante, mayor info)")

# PCA on the mean correlation matrix -> intrinsic dimensionality
ev = np.sort(np.linalg.eigvalsh(C.to_numpy()))[::-1]
cum = np.cumsum(ev)/ev.sum()
print("\n================  DIMENSIONALIDAD INTRÍNSECA (PCA sobre corr)  ================")
print("  varianza acumulada por componente:", np.round(cum, 3))
print(f"  componentes para 90% de la varianza: {int(np.argmax(cum>=0.90))+1} de {len(NAMES)}")

# ── FASE 2 — greedy minimal set (max info, redundancy brake |r|<0.70) ─────────
print("\n================  FASE 2 · CONJUNTO MÍNIMO (greedy, |r|<0.70)  ================")
Cn = C.to_numpy(); sel = []; rem = list(range(len(NAMES)))
while rem:
    elig = [i for i in rem if all(abs(Cn[i, s]) < 0.70 for s in sel)]
    if not elig:
        break
    best = max(elig, key=lambda i: info[i])
    sel.append(best); rem.remove(best)
    # variance explained by the selected block (PCA on their sub-correlation)
    sub = C.to_numpy()[np.ix_(sel, sel)]
    ve = np.sort(np.linalg.eigvalsh(sub))[::-1]
    print(f"  + {NAMES[best]:11s} info={info[best]:.3f}  (axis-repr)   |set|={len(sel)}")
absorbed = [NAMES[i] for i in range(len(NAMES)) if i not in sel]
print(f"\n  CONJUNTO MÍNIMO ({len(sel)}): {[NAMES[i] for i in sel]}")
print(f"  absorbidos por redundancia: {absorbed}")
# how much of the 11-var information the minimal set retains (variance proxy)
full_ev = np.linalg.eigvalsh(C.to_numpy());
print(f"  dimensionalidad intrínseca (PCA 90%) = {int(np.argmax(cum>=0.90))+1}  |  |mínimo| = {len(sel)}")
