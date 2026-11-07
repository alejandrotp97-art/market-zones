"""Monte Carlo touch probability for the zone target price (BTC-USD, ETH-USD).

Question: given the buy/sell target the panel shows today (consensus M1+M3 from
zones.target.compute), how likely is the price to TOUCH it within H days?

This is a study, not a panel feature. It only earns a place in the panel if it is
CALIBRATED: when it says 20%, the touch must happen about 20% of the time.

Method, per as-of date on a causal grid (history truncated at the as-of):
  1. target  = zones.target.compute(history) -> buy/sell consensus (same code as panel)
  2. MC      = stationary block bootstrap (Politis-Romano) of the log returns seen
               up to the as-of, N_PATHS paths, raw historical drift (no opinion
               added). P(touch) = share of paths whose running extreme crosses
               the target within H.
  3. GBM     = zero-drift reflection formula with the last-year daily vol: the
               "naive" reference the bootstrap has to beat.
  4. truth   = did the real closes in (as-of, as-of+H] touch the target?

Known approximation: the target is held FIXED along the path. In reality the zone
boundary moves (the moving average, drawdown and RSI move with the price), so
this measures "touch today's target price", not "enter the zone". Closes are
used on both sides (model and truth) so the comparison is consistent; intraday
lows/highs would touch more often than the close-to-close model can see.

Run:  .venv/bin/python research/mc_touch/mc_touch.py run      (~10 min)
      .venv/bin/python research/mc_touch/mc_touch.py report   (from rows.csv)
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import zlib

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SYMBOLS = ("BTC-USD", "ETH-USD")
YEARS = 25
STEP = 14                 # bars between as-of dates
MIN_BARS = 300            # history needed before the first as-of
HORIZONS = (30, 90)       # days ahead
N_PATHS = 10_000
MEAN_BLOCK = 20           # mean bootstrap block length (keeps vol clustering)
VOL_WIN = 252             # daily vol window for the GBM reference
BINS = (0.0, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 1.0)


# --- primitives ---------------------------------------------------------------

def bootstrap_paths(returns, n_paths: int, horizon: int, mean_block: float,
                    seed: int) -> np.ndarray:
    """Stationary block bootstrap: (n_paths, horizon) log returns drawn from
    `returns`. Each step continues the current block with prob 1 - 1/mean_block,
    otherwise jumps to a random index. Circular wrap at the end."""
    r = np.asarray(returns, dtype=float)
    n = len(r)
    rng = np.random.default_rng(seed)
    p_new = 0.0 if np.isinf(mean_block) else 1.0 / mean_block
    idx = rng.integers(0, n, size=n_paths)
    out = np.empty((n_paths, horizon))
    out[:, 0] = r[idx]
    for t in range(1, horizon):
        jump = rng.random(n_paths) < p_new
        idx = np.where(jump, rng.integers(0, n, size=n_paths), (idx + 1) % n)
        out[:, t] = r[idx]
    return out


def mc_touch_prob(price: float, target: float, paths: np.ndarray, side: str) -> float:
    """Share of paths whose running min (buy) / max (sell) reaches the target."""
    if (side == "buy" and target >= price) or (side == "sell" and target <= price):
        return 1.0
    cum = np.cumsum(paths, axis=1)
    lvl = np.log(target / price)
    hit = cum.min(axis=1) <= lvl if side == "buy" else cum.max(axis=1) >= lvl
    return float(hit.mean())


def gbm_touch_prob(price: float, target: float, sigma: float, horizon: int,
                   side: str) -> float:
    """Zero-drift reflection: P(touch) = 2 * Phi(-|ln(target/price)| / (sigma*sqrt(H)))."""
    if (side == "buy" and target >= price) or (side == "sell" and target <= price):
        return 1.0
    d = abs(np.log(target / price)) / (sigma * np.sqrt(horizon))
    return float(min(1.0, 2.0 * norm.cdf(-d)))


def pool_returns(close, dates, i: int, start=None) -> np.ndarray:
    """Finite log returns of closes[0..i] (causal), optionally only from the
    first bar on/after `start` (so no return straddles the pool boundary)."""
    c = np.asarray(close, dtype=float)[: i + 1]
    if start is not None:
        c = c[np.asarray(pd.DatetimeIndex(dates)[: i + 1] >= start)]
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.diff(np.log(c))
    return lr[np.isfinite(lr)]


def realized_touch(future_closes, target: float, side: str) -> bool:
    f = np.asarray(future_closes, dtype=float)
    return bool(f.min() <= target) if side == "buy" else bool(f.max() >= target)


def calibration(p, y, bins=BINS) -> dict:
    """Reliability table (non-empty bins only) + Brier score + skill vs the
    constant base rate. The base rate is the sample's own, so the skill is an
    upper bound for the reference, not an out-of-sample climatology."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    edges = np.asarray(bins, dtype=float)
    which = np.digitize(p, edges[1:-1], right=True)
    rows = []
    for b in range(len(edges) - 1):
        m = which == b
        if m.any():
            rows.append({"lo": float(edges[b]), "hi": float(edges[b + 1]),
                         "n": int(m.sum()), "pred": float(p[m].mean()),
                         "real": float(y[m].mean())})
    brier = float(np.mean((p - y) ** 2))
    base = float(y.mean())
    ref = base * (1.0 - base)
    return {"bins": rows, "brier": brier, "base": base,
            "skill": float(1.0 - brier / ref) if ref > 0 else float("nan")}


# --- study --------------------------------------------------------------------

def run(n_paths: int = N_PATHS, pool_start=None, min_pool: int = 0,
        tag: str = "") -> pd.DataFrame:
    """Full-history study (default). With `pool_start`, the bootstrap only
    resamples returns from that date (still causal), as-ofs begin once the pool
    holds `min_pool` returns, and the full-history MC is kept as `p_mc_full`
    on the same dates for a like-for-like comparison."""
    from zones import fetch_daily
    from zones.target import compute

    hmax = max(HORIZONS)
    rows = []
    for sym in SYMBOLS:
        df = fetch_daily(sym, years=YEARS).reset_index(drop=True)
        close = df["close"].to_numpy(float)
        dates = pd.DatetimeIndex(df["date"])
        t0 = time.time()
        first = MIN_BARS
        if pool_start is not None:
            first = max(first, int(np.searchsorted(dates, pool_start)) + min_pool)
        asofs = range(first, len(df) - min(HORIZONS), STEP)
        for k, i in enumerate(asofs):
            hist = df.iloc[: i + 1]
            res = compute(hist, sym)
            if res is None:
                continue
            price = float(close[i])
            lr_full = pool_returns(close, dates, i)
            sigma = float(np.std(lr_full[-VOL_WIN:], ddof=1))
            seed = zlib.crc32(f"{sym}:{i}".encode())
            lr = lr_full if pool_start is None else pool_returns(close, dates, i, pool_start)
            paths = bootstrap_paths(lr, n_paths, hmax, MEAN_BLOCK, seed)
            paths_full = (None if pool_start is None else
                          bootstrap_paths(lr_full, n_paths, hmax, MEAN_BLOCK, seed + 1))
            for side in ("buy", "sell"):
                tgt = res[side]["consensus"]
                if tgt is None:
                    continue
                inside = (tgt >= price) if side == "buy" else (tgt <= price)
                for h in HORIZONS:
                    if i + h >= len(df):
                        continue
                    row = {
                        "symbol": sym, "date": str(df["date"].iloc[i])[:10],
                        "side": side, "h": h, "price": price, "target": tgt,
                        "dist_pct": (tgt / price - 1.0) * 100.0,
                        "inside": inside, "model": res["model"],
                        "p_mc": mc_touch_prob(price, tgt, paths[:, :h], side),
                        "p_gbm": gbm_touch_prob(price, tgt, sigma, h, side),
                        "touched": realized_touch(close[i + 1: i + 1 + h], tgt, side),
                    }
                    if paths_full is not None:
                        row["p_mc_full"] = mc_touch_prob(price, tgt, paths_full[:, :h], side)
                    rows.append(row)
            if k % 25 == 0:
                print(f"{sym} {k}/{len(asofs)} {time.time() - t0:.0f}s", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(HERE / f"rows{tag}.csv", index=False)
    return out


MODELS = (("p_mc", "MC"), ("p_mc_full", "MC-full"), ("p_gbm", "GBM"))


def report(rows: pd.DataFrame, tag: str = "") -> dict:
    """Calibration per symbol x side x horizon (and pooled), excluding as-ofs
    already inside the zone (P=1 by definition, they would inflate the score).
    Every model column present in `rows` is scored on the same as-ofs."""
    models = [(c, name) for c, name in MODELS if c in rows.columns]
    live = rows[~rows["inside"].astype(bool)]
    out = {}
    groups = [(s, sd, h) for s in (*SYMBOLS, "ALL") for sd in ("buy", "sell")
              for h in HORIZONS]
    for sym, side, h in groups:
        g = live[(live["side"] == side) & (live["h"] == h)]
        if sym != "ALL":
            g = g[g["symbol"] == sym]
        if len(g) < 10:
            continue
        y = g["touched"].astype(float).to_numpy()
        key = f"{sym} {side} H{h}"
        out[key] = {"n": len(g), "n_eff": round(len(g) * min(1.0, STEP / h), 1)}
        for col, name in models:
            out[key][name] = calibration(g[col], y)
    (HERE / f"results{tag}.json").write_text(json.dumps(out, indent=2))
    for key, r in out.items():
        print(f"\n== {key}  n={r['n']} (n_eff~{r['n_eff']})  base={r['MC']['base']:.3f}")
        print("   Brier " + "   ".join(
            f"{name} {r[name]['brier']:.4f} (skill {r[name]['skill']:+.3f})"
            for _, name in models))
        print("   bin          n    MC pred  real")
        for b in r["MC"]["bins"]:
            print(f"   {b['lo']:.2f}-{b['hi']:.2f}  {b['n']:4d}   {b['pred']:.3f}   {b['real']:.3f}")
    return out


RECENT_START = pd.Timestamp("2021-01-01", tz="UTC")
RECENT_MIN_POOL = 365     # one year of returns before the first as-of
RECENT_PATHS = 50_000

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        report(run())
    elif cmd == "report":
        report(pd.read_csv(HERE / "rows.csv"))
    elif cmd == "recent":
        report(run(RECENT_PATHS, RECENT_START, RECENT_MIN_POOL, "_2021"), "_2021")
    elif cmd == "recent-report":
        report(pd.read_csv(HERE / "rows_2021.csv"), "_2021")
    else:
        sys.exit(f"unknown command: {cmd} (use run | report | recent | recent-report)")
