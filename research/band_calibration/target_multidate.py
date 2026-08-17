"""Estudio multi-fecha del precio objetivo por zona (¿hay sesgo que calibrar?).

Para cada activo, recorre 25 años en una grilla causal (as-of cada STEP barras):
en cada as-of trunca la historia, calcula el objetivo de COMPRA (leer Capitulación)
y de VENTA (leer Euforia) que el panel habría mostrado, y mira SOLO hacia adelante
una ventana H: ¿cruzó el objetivo? ¿cuánto lo perforó/superó respecto del extremo?

La pregunta de calibración: el error del suelo, ¿es un SESGO estable (mediana
lejos de 0 con dispersión chica -> se calibra restando) o DISPERSIÓN (mediana
cerca de 0 pero colas gordas por cracks idiosincráticos -> NO se calibra)?

Scorer magro: consenso = mediana(M1, M3), idéntico a zones.target.compute pero sin
M2 (no vota) ni la curva del gráfico. Se valida contra el compute real abajo.
"""
import pathlib
import sys
import time

import numpy as np
import pandas as pd

# raíz del repo desde la ubicación del script: clavar una ruta absoluta
# ataba el estudio a una máquina concreta y no corría en un clon.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from zones import analyze, fetch_daily
from zones.engine import DAILY

BUY_U, SELL_U = 20.0, 80.0
VOL_W = 0.10
ITERS = 10          # 2^10 ~ 0.1% price resolution, de sobra para un error en %


def lean_targets(df):
    """(score, zone_name, buy_consensus, sell_consensus) o None si no clasifica."""
    df = df.reset_index(drop=True)
    frame, s = analyze(df, vol_weight=VOL_W)
    if s.model == "none":
        return None
    price = float(s.close)
    li = len(df) - 1
    d0 = df.copy()
    cache: dict[float, float] = {}

    def ev(P):
        k = round(float(P), 4)
        if k in cache:
            return cache[k]
        d0.loc[li, "close"] = float(P)
        f, _ = analyze(d0, vol_weight=VOL_W)
        v = float(f.iloc[-1]["score_raw"])
        cache[k] = v
        return v

    def solve(target, lo, hi):
        glo, ghi = ev(lo), ev(hi)
        for _ in range(40):
            if glo <= target <= ghi:
                break
            if target < glo:
                hi, ghi = lo, glo
                lo *= 0.6
                glo = ev(lo)
            else:
                lo, glo = hi, ghi
                hi *= 1.6
                ghi = ev(hi)
            if lo < 1e-9 or hi > price * 1e5:
                return None
        for _ in range(ITERS):
            mid = (lo + hi) / 2.0
            if ev(mid) < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    m1_buy = solve(BUY_U, price * 0.4, price)
    m1_sell = solve(SELL_U, price, price * 2.0)

    close = frame["close"].astype(float).to_numpy()
    ma_win = DAILY.stretch_full if s.model == "full" else DAILY.stretch_reduced
    ma = pd.Series(close).rolling(ma_win, min_periods=ma_win).mean().to_numpy()
    mayer = close / ma
    score_s = frame["score"].astype(float).to_numpy()
    ma_today = float(ma[-1])

    def analog(is_buy):
        thr, wide = (BUY_U, 25.0) if is_buy else (SELL_U, 75.0)
        mask = (score_s <= thr) if is_buy else (score_s >= thr)
        m = mayer[mask & np.isfinite(mayer)]
        if len(m) < 10:
            mask = (score_s <= wide) if is_buy else (score_s >= wide)
            m = mayer[mask & np.isfinite(mayer)]
        if len(m) < 5 or not np.isfinite(ma_today):
            return None
        return float(np.nanquantile(m, 0.50)) * ma_today

    def cons(m1, m3):
        pts = sorted(p for p in (m1, m3) if p is not None)
        if not pts:
            return None
        n = len(pts)
        return pts[n // 2] if n % 2 else (pts[0] + pts[1]) / 2.0

    return s.score, s.zone_name, cons(m1_buy, analog(True)), cons(m1_sell, analog(False))


# ---------------------------------------------------------------------------
# Runner multi-fecha, paralelo por activo.
# ---------------------------------------------------------------------------
STEP = 63           # as-of cada ~trimestre
H = 504             # ventana hacia adelante ~2 años hábiles
MIN_FWD = 189       # descarto colas con < ~9 meses de futuro
START = 500         # primera as-of: >=500 barras => sobre todo modelo full

# tramo de la ESCALERA -> símbolo Yahoo
UNIVERSE = [
    ("index",  "^GSPC"), ("index",  "^RUT"), ("index", "^IXIC"),
    ("mega",   "AAPL"),  ("mega",   "MSFT"),
    ("quality", "WMT"),  ("quality", "JNJ"), ("quality", "V"), ("quality", "BRK-B"),
    ("growth", "NVDA"),  ("growth", "MU"),   ("growth", "LLY"), ("growth", "TSLA"),
    ("small",  "EAT"),   ("small",  "UMBF"), ("small", "VSAT"), ("small", "MOG-A"),
    ("crypto", "BTC-USD"),
]


def run_asset(job):
    tier, sym = job
    try:
        raw = fetch_daily(sym, years=25)
    except Exception as e:
        return tier, sym, f"ERROR {type(e).__name__}: {str(e)[:50]}", []
    raw["date"] = pd.to_datetime(raw["date"], utc=True)
    lo_arr = raw["low"].astype(float).to_numpy() if "low" in raw else raw["close"].astype(float).to_numpy()
    hi_arr = raw["high"].astype(float).to_numpy() if "high" in raw else raw["close"].astype(float).to_numpy()
    n = len(raw)
    rows = []
    for i in range(START, n - MIN_FWD, STEP):
        hist = raw.iloc[:i].reset_index(drop=True)
        out = lean_targets(hist)
        if out is None:
            continue
        score, zone, buy_t, sell_t = out
        spot = float(raw["close"].iloc[i - 1])
        j = min(i + H, n)
        fmin = float(lo_arr[i:j].min())
        fmax = float(hi_arr[i:j].max())
        b_hit = (buy_t is not None) and (fmin <= buy_t)
        s_hit = (sell_t is not None) and (fmax >= sell_t)
        rows.append({
            "tier": tier, "sym": sym,
            "date": str(raw["date"].iloc[i - 1].date()),
            "score": round(score, 1), "zone": zone, "spot": spot,
            "buy_t": buy_t, "sell_t": sell_t, "hbars": j - i,
            "fmin": fmin, "fmax": fmax,
            "b_hit": int(b_hit), "s_hit": int(s_hit),
            "b_over": (fmin - buy_t) / buy_t if buy_t else None,   # <=0 si hit
            "s_over": (fmax - sell_t) / sell_t if sell_t else None,  # >=0 si hit
        })
    return tier, sym, f"ok n={len(rows)}", rows


def main_run():
    import multiprocessing as mp
    t0 = time.time()
    out_rows = []
    with mp.Pool(4) as pool:
        for tier, sym, status, rows in pool.imap_unordered(run_asset, UNIVERSE):
            out_rows.extend(rows)
            print(f"  {sym:8} [{tier:7}] {status}  ({time.time()-t0:.0f}s)", flush=True)
    df = pd.DataFrame(out_rows)
    df.to_csv("multidate_rows.csv", index=False)
    print(f"\nguardado multidate_rows.csv  filas={len(df)}  en {time.time()-t0:.0f}s")


if __name__ == "__main__" and sys.argv[1:2] == ["run"]:
    main_run()
    sys.exit()

if __name__ == "__main__" and sys.argv[1:2] == ["validate"]:
    # Confirmar que el scorer magro == consenso del compute real, en varias fechas.
    from zones.target import compute as target_compute
    raw = fetch_daily("^GSPC", years=25)
    raw["date"] = pd.to_datetime(raw["date"], utc=True)
    print(f"{'idx':>5} {'lean_buy':>10} {'full_buy':>10} {'lean_sell':>10} {'full_sell':>10}")
    for i in (1500, 3000, 4500, 6000):
        hist = raw.iloc[:i].reset_index(drop=True)
        lean = lean_targets(hist)
        full = target_compute(hist, "^GSPC")
        lb, ls = (lean[2], lean[3]) if lean else (None, None)
        fb, fs = (full["buy"]["consensus"], full["sell"]["consensus"]) if full else (None, None)
        print(f"{i:>5} {lb or 0:>10.1f} {fb or 0:>10.1f} {ls or 0:>10.1f} {fs or 0:>10.1f}")
    t = time.time()
    lean_targets(raw.iloc[:4500].reset_index(drop=True))
    print(f"\nlean_targets once: {(time.time()-t)*1000:.0f}ms")
