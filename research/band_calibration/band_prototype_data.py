"""Calibra la BANDA de incertidumbre desde la volatilidad realizada y arma los
datos del prototipo.

Idea (del estudio multi-fecha): la dispersión de la perforación del suelo escala
con la volatilidad del activo. Entonces la banda NO es un tier-label: es
    profundidad_suelo = k_buy  * vol_hoy      (la zona corre HACIA ABAJO del objetivo)
    altura_techo      = k_sell * vol_hoy      (la zona corre HACIA ARRIBA del objetivo)
con k ajustado para que reproduzca la mediana observada de perforación/superación.

Primero prueba que vol PREDICE la dispersión (Spearman avg_vol vs mediana obs.).
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/alex/bots/market-zones")
from zones import fetch_daily
from zones.target import compute as target_compute

rows = pd.read_csv("multidate_rows.csv")
TRADING = 252


def ann_vol_series(close):
    lr = np.log(close).diff()
    return lr.rolling(TRADING).std() * np.sqrt(TRADING)


# --- 1. por activo: mediana observada (hits) + vol media e instantánea ----------
cal = []
CACHE = {}
for sym in rows["sym"].drop_duplicates():
    s = rows[rows["sym"] == sym]
    b = s[s["b_hit"] == 1]["b_over"].to_numpy(float)
    v = s[s["s_hit"] == 1]["s_over"].to_numpy(float)
    med_b = float(np.nanmedian(np.abs(b))) if len(b) else np.nan   # magnitud
    med_s = float(np.nanmedian(v)) if len(v) else np.nan
    raw = fetch_daily(sym, years=25)
    CACHE[sym] = raw
    close = raw["close"].astype(float).reset_index(drop=True)
    av = ann_vol_series(close)
    avg_vol = float(np.nanmean(av))
    vol_now = float(av.iloc[-1])
    cal.append({"sym": sym, "tier": s["tier"].iloc[0], "med_b": med_b,
                "med_s": med_s, "avg_vol": avg_vol, "vol_now": vol_now})
cal = pd.DataFrame(cal)

# Spearman: ¿vol media ordena la perforación mediana igual?
from scipy.stats import spearmanr
rho_b, p_b = spearmanr(cal["avg_vol"], cal["med_b"], nan_policy="omit")
rho_s, p_s = spearmanr(cal["avg_vol"], cal["med_s"], nan_policy="omit")

k_buy = float(np.nanmedian(cal["med_b"] / cal["avg_vol"]))
k_sell = float(np.nanmedian(cal["med_s"] / cal["avg_vol"]))

print("=" * 74)
print("CALIBRACIÓN vol -> banda (18 activos, medianas del estudio de 25 años)")
print("=" * 74)
print(f"  Spearman(avg_vol, |perforación suelo|) = {rho_b:+.2f}  (p={p_b:.3f})")
print(f"  Spearman(avg_vol,  superación techo)   = {rho_s:+.2f}  (p={p_s:.3f})")
print(f"  k_buy  (profundidad_suelo / vol) = {k_buy:.2f}")
print(f"  k_sell (altura_techo      / vol) = {k_sell:.2f}")
print("-" * 74)
print(f"{'sym':8} {'tier':8} {'vol_now':>8} {'|perf|obs':>10} {'perf_pred':>10}")
for _, r in cal.sort_values("avg_vol").iterrows():
    pred = k_buy * r["vol_now"]
    print(f"{r['sym']:8} {r['tier']:8} {r['vol_now']*100:>7.0f}% "
          f"{r['med_b']*100:>9.0f}% {pred*100:>9.0f}%")


def confidence(vol_now):
    if vol_now < 0.25:
        return "fiable"
    if vol_now < 0.45:
        return "media"
    return "amplia"


# --- 2. tarjetas del prototipo: 6 activos, uno por tramo ------------------------
CARDS = [("^GSPC", "S&P 500", "índice"),
         ("JNJ", "Johnson & Johnson", "quality"),
         ("AAPL", "Apple", "mega"),
         ("NVDA", "NVIDIA", "growth"),
         ("VSAT", "Viasat", "small-cap"),
         ("BTC-USD", "Bitcoin", "crypto")]

out = []
for sym, name, tier in CARDS:
    raw = CACHE[sym]
    raw = raw.copy()
    raw["date"] = pd.to_datetime(raw["date"], utc=True)
    hist = raw.reset_index(drop=True)
    blk = target_compute(hist, sym)
    close = hist["close"].astype(float).reset_index(drop=True)
    vol_now = float(ann_vol_series(close).iloc[-1])
    price = float(blk["price"])
    buy = blk["buy"]["consensus"]
    sell = blk["sell"]["consensus"]
    depth = k_buy * vol_now      # fracción hacia abajo del objetivo de compra
    height = k_sell * vol_now    # fracción hacia arriba del objetivo de venta
    card = {
        "sym": sym, "name": name, "tier": tier, "ccy": blk["ccy"],
        "price": price, "score": blk["score"], "zone": None,
        "vol": round(vol_now * 100, 0), "conf": confidence(vol_now),
        "buy": buy, "buy_floor": (buy * (1 - depth)) if buy else None,
        "sell": sell, "sell_top": (sell * (1 + height)) if sell else None,
    }
    out.append(card)
    print(f"\n{name} ({sym}) vol={vol_now*100:.0f}% conf={card['conf']}")
    print(f"  precio {price:,.2f}  score {blk['score']}")
    if buy:
        print(f"  COMPRA entrada {buy:,.2f}  -> fondo típico {card['buy_floor']:,.2f}  (−{depth*100:.0f}%)")
    if sell:
        print(f"  VENTA  entrada {sell:,.2f}  -> techo típico {card['sell_top']:,.2f}  (+{height*100:.0f}%)")

with open("band_cards.json", "w") as f:
    json.dump({"k_buy": k_buy, "k_sell": k_sell, "rho_b": rho_b, "rho_s": rho_s,
               "cards": out}, f, indent=2)
print("\nguardado band_cards.json")
