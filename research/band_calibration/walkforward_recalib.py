"""Comprobación OUT-OF-SAMPLE de la tesis: ¿k FIJA vs k RECALIBRADA predice mejor
el ancho de banda? Sobre acciones e índices reales, walk-forward.

Predicción de banda en el as-of a_i:  depth_i = k * vol_i   (vol_i = vol 252d en a_i,
conocida sin lookahead). El resultado real y_i = |perforación| solo se sabe 2 años
después (ventana forward). Entonces la k utilizable en a_i solo puede ajustarse con
muestras cuya ventana YA maduró: as-of <= a_i - 730d.

  FIJA     : k ajustada una vez, en la primera fecha de test, y congelada.
  SEMANAL  : k re-ajustada en cada as-of con TODO lo madurado hasta ese momento.

Métrica: error absoluto |k*vol_i - y_i| en fracción de perforación. Menor = mejor.
Si SEMANAL no baja el error => recalibrar seguido no aporta (tesis confirmada).
"""
import pathlib
import sys

import numpy as np
import pandas as pd

# raíz del repo desde la ubicación del script: clavar una ruta absoluta
# ataba el estudio a una máquina concreta y no corría en un clon.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from zones import fetch_daily

TRADING, LAG = 252, pd.Timedelta(days=730)
INDICES = {"^GSPC", "^RUT", "^IXIC"}

rows = pd.read_csv("multidate_rows.csv")
rows["asof"] = pd.to_datetime(rows["date"])

# --- vol 252d en cada as-of (sin lookahead) --------------------------------------
vol_at = {}
for sym in rows["sym"].drop_duplicates():
    raw = fetch_daily(sym, years=25)
    raw["date"] = pd.to_datetime(raw["date"])
    close = raw["close"].astype(float)
    av = (np.log(close).diff().rolling(TRADING).std() * np.sqrt(TRADING))
    vol_at[sym] = pd.Series(av.values, index=raw["date"].values)

def vol_on(sym, d):
    s = vol_at[sym]
    s = s[s.index <= np.datetime64(d)]
    return float(s.iloc[-1]) if len(s) and np.isfinite(s.iloc[-1]) else np.nan

rows["vol"] = [vol_on(r.sym, r.asof) for r in rows.itertuples()]

# muestras con resultado (hit) y vol válida
buy = rows[(rows["b_hit"] == 1) & rows["vol"].notna()].copy()
buy["y"] = buy["b_over"].abs()
sell = rows[(rows["s_hit"] == 1) & rows["vol"].notna()].copy()
sell["y"] = sell["s_over"]


def fit_k(df):
    r = (df["y"] / df["vol"]).to_numpy(float)
    r = r[np.isfinite(r)]
    return float(np.median(r)) if len(r) else np.nan


def walk(df, label, min_train=40):
    df = df.sort_values("asof").reset_index(drop=True)
    k_fixed = None
    recs = []
    for r in df.itertuples():
        train = df[df["asof"] <= r.asof - LAG]
        if len(train) < min_train:
            continue
        k_w = fit_k(train)
        if k_fixed is None:
            k_fixed = k_w                       # congelo la PRIMERA k utilizable
        pred_w, pred_f = k_w * r.vol, k_fixed * r.vol
        recs.append((r.sym, r.asof, r.y, r.vol, pred_w, pred_f, k_w,
                     r.sym in INDICES))
    t = pd.DataFrame(recs, columns=["sym", "asof", "y", "vol", "pw", "pf", "kw", "idx"])
    if not len(t):
        print(f"{label}: sin muestras de test"); return t
    ew = (t["pw"] - t["y"]).abs()
    ef = (t["pf"] - t["y"]).abs()
    print(f"\n===== {label}  (n_test={len(t)}, {t['asof'].min().date()}..{t['asof'].max().date()}) =====")
    print(f"  k FIJA (congelada en {t['asof'].min().date()}) = {t['pf'].iloc[0]/t['vol'].iloc[0]:.3f}"
          f"   |   k SEMANAL hoy = {t['kw'].iloc[-1]:.3f}   (rango {t['kw'].min():.3f}..{t['kw'].max():.3f})")
    print(f"  MAE  FIJA = {ef.mean():.4f}     MAE SEMANAL = {ew.mean():.4f}     "
          f"mejora semanal = {(ef.mean()-ew.mean())/ef.mean()*100:+.2f}%")
    print(f"  MedAE FIJA= {ef.median():.4f}   MedAE SEMANAL={ew.median():.4f}")
    for name, mask in [("INDICES", t["idx"]), ("ACCIONES", ~t["idx"])]:
        if mask.sum():
            efm, ewm = ef[mask].mean(), ew[mask].mean()
            print(f"    {name:9} n={int(mask.sum()):4}  MAE fija={efm:.4f}  semanal={ewm:.4f}  "
                  f"mejora={((efm-ewm)/efm*100):+.2f}%")
    return t


print(f"muestras buy-hit={len(buy)}  sell-hit={len(sell)}")
tb = walk(buy, "SUELO  (perforación de compra)")
ts = walk(sell, "TECHO  (superación de venta)")

# trayectoria anual de k semanal para ver la (in)estabilidad
print("\n--- trayectoria de k SEMANAL por año (suelo) ---")
b = buy.sort_values("asof").reset_index(drop=True)
for yr in range(2012, 2025, 2):
    V = pd.Timestamp(f"{yr}-08-01")
    tr = b[b["asof"] <= V - LAG]
    if len(tr) >= 40:
        print(f"  al {V.date()}: k_buy={fit_k(tr):.3f}  (n_train={len(tr)})")
