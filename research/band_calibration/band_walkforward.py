"""Walk-forward check of the target band (K_BUY / K_SELL in zones/target.py).

The shipped band is  depth = k * vol  with vol = last-year annualised vol AT THE
AS-OF (same formula as the panel). The original fit used each asset's
full-history AVERAGE vol and one point per asset (18 points). This study refits k
per sample, with the vol the panel actually sees, and scores every candidate
OUT OF SAMPLE: a test as-of in year Y only trains on samples whose forward window
had closed before Y-01-01.

Band meaning (unchanged): the typical depth the price runs INTO the zone once it
touches the target -> the median among touches. Loss = pinball q=0.5 (= MAE/2);
coverage = share of touches whose depth stayed within the band (ideal 0.50).

Candidates:
  current  k fixed at the shipped value (fitted in-sample: favoured, not penalised)
  global   k = median(depth / vol) over the closed training samples
  class    same, per asset class (index/mega/quality/growth/small/crypto);
           falls back to global when the class has < min_class training samples
  const    depth = median training depth, no vol (shows whether vol helps)

Run from the repo root:
  .venv/bin/python research/band_calibration/band_walkforward.py run    (~6 min)
  .venv/bin/python research/band_calibration/band_walkforward.py eval
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

PPY = 252                       # = zones.engine.ZMIN_FULL, the panel's vol window
K_BUY, K_SELL, MAX_DEPTH = 0.64, 1.16, 0.90     # shipped values (zones/target.py)
MIN_TRAIN, MIN_CLASS = 60, 30
ROWS = HERE / "walkforward_rows.csv"


# --- primitives ---------------------------------------------------------------

def asof_vol(close, ppy: int = PPY, window: int | None = None) -> float:
    """Annualised vol of the last `window` (default `ppy`) finite log returns.
    With the defaults this is exactly the panel's formula."""
    c = np.asarray(close, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.diff(np.log(c))
    lr = lr[np.isfinite(lr)]
    w = min(window or ppy, len(lr))
    return float(np.std(lr[-w:], ddof=1) * np.sqrt(ppy)) if w > 5 else float("nan")


def pinball(pred, y, q: float = 0.5) -> float:
    pred = np.asarray(pred, dtype=float)
    y = np.asarray(y, dtype=float)
    d = y - pred
    return float(np.mean(np.maximum(q * d, (q - 1.0) * d)))


def fit_k(depth, vol) -> float:
    depth = np.asarray(depth, dtype=float)
    vol = np.asarray(vol, dtype=float)
    ok = np.isfinite(depth) & np.isfinite(vol) & (vol > 0)
    return float(np.median(depth[ok] / vol[ok])) if ok.any() else float("nan")


def fit_l1(y, X) -> np.ndarray:
    """Least-absolute-deviation fit  y ~ c0 + X @ c  (conditional median),
    solved exactly as a linear program. Returns [c0, c...]."""
    from scipy.optimize import linprog

    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float).reshape(len(y), -1)
    n, m = X.shape
    # vars: c0, c (m), u (n), v (n) ; min sum(u+v) ; c0 + X c + u - v = y
    cost = np.r_[np.zeros(m + 1), np.ones(2 * n)]
    A = np.hstack([np.ones((n, 1)), X, np.eye(n), -np.eye(n)])
    bounds = [(None, None)] * (m + 1) + [(0, None)] * (2 * n)
    r = linprog(cost, A_eq=A, b_eq=y, bounds=bounds, method="highs")
    return r.x[: m + 1]


def fit_power(depth, vol, floor: float = 1e-4) -> tuple[float, float]:
    """Median regression of log(depth) on log(vol): depth = a * vol**b.
    The median commutes with the monotone log, so this is the conditional
    median of depth itself."""
    depth = np.asarray(depth, dtype=float)
    vol = np.asarray(vol, dtype=float)
    ok = np.isfinite(depth) & np.isfinite(vol) & (vol > 0)
    c = fit_l1(np.log(np.maximum(depth[ok], floor)), np.log(vol[ok]))
    return float(np.exp(c[0])), float(c[1])


def _cap(x, cap):
    return np.minimum(x, cap) if cap is not None else x


def walkforward(rows: pd.DataFrame, k_current: float, cap, *,
                min_train: int = MIN_TRAIN, min_class: int = MIN_CLASS,
                extra: str | None = None, floor: float = 1e-4) -> pd.DataFrame:
    """rows: date, end (last forward bar), cls, depth, vol. Returns the test rows
    (those with >= min_train closed training samples) with one prediction per
    candidate. With `extra` (a positive column z), adds
    pred_extra = vol * a * z**g, fitted as the median of log(depth/vol)."""
    rows = rows.sort_values("date").reset_index(drop=True)
    out = []
    for year in sorted(rows["date"].dt.year.unique()):
        cutoff = pd.Timestamp(year=int(year), month=1, day=1)
        test = rows[rows["date"].dt.year == year]
        train = rows[rows["end"] < cutoff]
        if len(train) < min_train:
            continue
        k_g = fit_k(train["depth"], train["vol"])
        d_c = float(np.median(train["depth"]))
        a_p, b_p = fit_power(train["depth"], train["vol"])
        k_cls = {c: fit_k(g["depth"], g["vol"]) for c, g in train.groupby("cls")
                 if len(g) >= min_class}
        t = test.copy()
        v = t["vol"].to_numpy(float)
        t["pred_current"] = _cap(k_current * v, cap)
        t["pred_global"] = _cap(k_g * v, cap)
        t["pred_class"] = _cap(np.array([k_cls.get(c, k_g) for c in t["cls"]]) * v, cap)
        t["pred_const"] = d_c
        t["pred_power"] = _cap(a_p * v ** b_p, cap)
        t["power_a"], t["power_b"] = a_p, b_p
        t["k_global"] = k_g
        if extra is not None:
            ok = np.isfinite(train[extra]) & (train[extra] > 0)
            tr = train[ok]
            a_x, g_x = fit_l1(np.log(np.maximum(tr["depth"], floor) / tr["vol"]),
                              np.log(tr[[extra]].to_numpy(float)))
            t["pred_extra"] = _cap(v * np.exp(a_x) * t[extra].to_numpy(float) ** g_x, cap)
            t["extra_a"], t["extra_g"] = float(np.exp(a_x)), float(g_x)
        out.append(t)
    return pd.concat(out, ignore_index=True) if out else rows.iloc[0:0]


# --- dataset ------------------------------------------------------------------

def run_asset(job):
    from target_multidate import MIN_FWD, START, STEP, H, lean_targets

    from zones import fetch_daily

    tier, sym = job
    try:
        raw = fetch_daily(sym, years=25)
    except Exception as e:      # network / delisted: report, keep the others
        return sym, f"ERROR {type(e).__name__}", []
    raw["date"] = pd.to_datetime(raw["date"], utc=True)
    close = raw["close"].astype(float).to_numpy()
    lo = raw["low"].astype(float).to_numpy()
    hi = raw["high"].astype(float).to_numpy()
    n = len(raw)
    rows = []
    for i in range(START, n - MIN_FWD, STEP):
        out = lean_targets(raw.iloc[:i].reset_index(drop=True))
        if out is None:
            continue
        _, _, buy_t, sell_t = out
        j = min(i + H, n)
        fmin, fmax = float(np.nanmin(lo[i:j])), float(np.nanmax(hi[i:j]))
        rows.append({
            "cls": tier, "sym": sym,
            "date": str(raw["date"].iloc[i - 1].date()),
            "end": str(raw["date"].iloc[j - 1].date()),
            "spot": float(close[i - 1]), "vol": asof_vol(close[:i]),
            "vol_63": asof_vol(close[:i], window=63),
            "vol_756": asof_vol(close[:i], window=756),
            "buy_t": buy_t, "sell_t": sell_t, "fmin": fmin, "fmax": fmax,
        })
    return sym, f"ok n={len(rows)}", rows


def run():
    import multiprocessing as mp

    from target_multidate import UNIVERSE
    jobs = [*UNIVERSE, ("crypto", "ETH-USD")]
    t0, rows = time.time(), []
    with mp.Pool(4) as pool:
        for sym, status, r in pool.imap_unordered(run_asset, jobs):
            rows.extend(r)
            print(f"  {sym:8} {status}  ({time.time() - t0:.0f}s)", flush=True)
    pd.DataFrame(rows).to_csv(ROWS, index=False)
    print(f"saved {ROWS.name} rows={len(rows)}")


# --- evaluation ---------------------------------------------------------------

def side_rows(df: pd.DataFrame, side: str) -> pd.DataFrame:
    """Touches only, with the realised depth into the zone."""
    if side == "buy":
        d = df[df["buy_t"].notna() & (df["fmin"] <= df["buy_t"])]
        depth = (d["buy_t"] - d["fmin"]) / d["buy_t"]
    else:
        d = df[df["sell_t"].notna() & (df["fmax"] >= df["sell_t"])]
        depth = (d["fmax"] - d["sell_t"]) / d["sell_t"]
    out = d.assign(depth=depth)
    return out[np.isfinite(out["vol"]) & (out["vol"] > 0)]


CANDS = ("current", "global", "class", "const", "power")
VOL_COLS = ("vol", "vol_63", "vol_756")


def score(t: pd.DataFrame) -> dict:
    y = t["depth"].to_numpy(float)
    return {c: {"pinball": pinball(t[f"pred_{c}"], y),
                "coverage": float(np.mean(y <= t[f"pred_{c}"].to_numpy(float)))}
            for c in CANDS} | {"n": len(t)}


def evaluate(df: pd.DataFrame) -> dict:
    """Score every candidate for each vol window. `current` is only meaningful
    on `vol` (the shipped window); on the others it shows the shipped k applied
    to a vol it was not fitted for."""
    df = df.assign(date=pd.to_datetime(df["date"]), end=pd.to_datetime(df["end"]))
    res = {}
    for side, k, cap in (("buy", K_BUY, MAX_DEPTH), ("sell", K_SELL, None)):
        s = side_rows(df, side)
        hits = len(s) / max(1, df[f"{side}_t"].notna().sum())
        res[side] = {"touch_rate": hits}
        for vc in VOL_COLS:
            sv = s[np.isfinite(s[vc]) & (s[vc] > 0)]
            sv = sv.assign(vol=sv[vc])
            t = walkforward(sv, k, cap)
            last = t.iloc[-1]
            res[side][vc] = {"all": score(t),
                             "k_global_last": float(last["k_global"]),
                             "power_last": [float(last["power_a"]), float(last["power_b"])],
                             "by_class": {c: score(g) for c, g in t.groupby("cls")}}
            r = res[side][vc]
            print(f"\n=== {side.upper()} [{vc}]  touches {len(s)} ({hits:.0%})  OOS n={len(t)}"
                  f"  k={r['k_global_last']:.2f}  power a={r['power_last'][0]:.2f}"
                  f" b={r['power_last'][1]:.2f}")
            print(f"   {'class':8} {'n':>4} " + " ".join(f"{c:>17}" for c in CANDS))
            for name, sc in [("ALL", r["all"]), *r["by_class"].items()]:
                print(f"   {name:8} {sc['n']:>4} " + " ".join(
                    f"{sc[c]['pinball']:.4f} cov{sc[c]['coverage']:.2f}" for c in CANDS))
    (HERE / "walkforward_results.json").write_text(json.dumps(res, indent=2))
    return res


# Candidate extra factors, all known at the as-of (L4 in the ODD log).
EXTRAS = {
    "vratio": lambda d: d["vol_63"] / d["vol_756"],   # recent vs long vol
    "s_dist": lambda d: d["sell_t"] / d["spot"],      # how far the sell target is
    "b_dist": lambda d: d["spot"] / d["buy_t"],       # how far above the buy target
}


def evaluate_extra(df: pd.DataFrame) -> dict:
    """Shipped band vs  k*vol*z**g  for each extra factor z, same OOS rows."""
    df = df.assign(date=pd.to_datetime(df["date"]), end=pd.to_datetime(df["end"]))
    for name, f in EXTRAS.items():
        df[name] = f(df)
    res = {}
    for side, k, cap in (("buy", K_BUY, MAX_DEPTH), ("sell", K_SELL, None)):
        s = side_rows(df, side)
        for name in EXTRAS:
            sv = s[np.isfinite(s[name]) & (s[name] > 0)]
            t = walkforward(sv, k, cap, extra=name)
            y = t["depth"].to_numpy(float)
            row = {"n": len(t), "g_last": float(t["extra_g"].iloc[-1])}
            for c in ("current", "extra"):
                row[c] = {"pinball": pinball(t[f"pred_{c}"], y),
                          "coverage": float(np.mean(y <= t[f"pred_{c}"].to_numpy(float)))}
            row["by_class"] = {
                c: {m: pinball(g[f"pred_{m}"], g["depth"]) for m in ("current", "extra")}
                for c, g in t.groupby("cls")}
            res[f"{side}:{name}"] = row
            gain = 1 - row["extra"]["pinball"] / row["current"]["pinball"]
            print(f"{side:4} {name:7} n={row['n']:4d} g={row['g_last']:+.2f}  "
                  f"current {row['current']['pinball']:.4f} cov{row['current']['coverage']:.2f}  "
                  f"extra {row['extra']['pinball']:.4f} cov{row['extra']['coverage']:.2f}  "
                  f"gain {gain:+.1%}")
            print("            " + "  ".join(
                f"{c}:{1 - v['extra'] / v['current']:+.0%}" for c, v in row["by_class"].items()))
    (HERE / "walkforward_extra.json").write_text(json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "eval"
    if cmd == "run":
        run()
    elif cmd == "eval":
        evaluate(pd.read_csv(ROWS))
    elif cmd == "extra":
        evaluate_extra(pd.read_csv(ROWS))
    else:
        sys.exit(f"unknown command: {cmd} (use run | eval | extra)")
