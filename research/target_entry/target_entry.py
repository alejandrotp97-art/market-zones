"""How close is the target to the price at which the asset REALLY enters the zone?

The panel's target is the price that would make TODAY's index read the zone
(buy = Capitulación entry, score 20; sell = Euforia entry, score 80). It is not a
forecast of when, but it implicitly claims WHERE the zone begins. This study
measures that claim on a causal grid:

  as-of i (history truncated at i)   -> zones.target.compute: M1, M3 mid, consensus
  first later bar j <= i+H where the shown score crosses the threshold
                                     -> entry price = close[j]
  error = log(entry / target)        (only as-ofs that were not already inside)

Candidates: M1 (model inversion), M3 (historical Mayer analog), consensus
(shipped: median of the two = their mean), and MIX = geometric blend
M1^w * M3^(1-w) with w fitted walk-forward on entries already observed.

The score is the causal, EMA-smoothed number the panel displays; `score_raw`
(pre-EMA, what M1 inverts) is reported as a second definition.

Run from the repo root:
  .venv/bin/python research/target_entry/target_entry.py run    (~8 min)
  .venv/bin/python research/target_entry/target_entry.py eval
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
sys.path.insert(0, str(HERE.parents[1] / "research" / "band_calibration"))

BUY_U, SELL_U = 20.0, 80.0
START, STEP, H = 500, 63, 504
GRID = np.round(np.arange(0.0, 1.0001, 0.05), 2)
ROWS = HERE / "rows.csv"


# --- primitives ---------------------------------------------------------------

def first_entry(score, i: int, h: int, thr: float, side: str) -> int:
    """First index j in [i, i+h) with score <= thr (buy) / >= thr (sell); -1 if none."""
    s = np.asarray(score, dtype=float)[i: i + h]
    with np.errstate(invalid="ignore"):
        hit = s <= thr if side == "buy" else s >= thr
    idx = np.flatnonzero(hit & np.isfinite(s))
    return int(i + idx[0]) if len(idx) else -1


def mix(m1, m3, w):
    return np.exp(w * np.log(m1) + (1.0 - w) * np.log(m3))


def fit_weight(m1, m3, entry) -> float:
    """Grid weight minimising the mean absolute log error to the entries."""
    m1, m3, entry = (np.asarray(a, dtype=float) for a in (m1, m3, entry))
    errs = [np.mean(np.abs(np.log(entry / mix(m1, m3, w)))) for w in GRID]
    return float(GRID[int(np.argmin(errs))])


def walkforward_mix(rows: pd.DataFrame, min_train: int = 30) -> pd.DataFrame:
    """Test year Y uses w fitted on rows whose entry happened before Y-01-01."""
    out = []
    for year in sorted(rows["date"].dt.year.unique()):
        cutoff = pd.Timestamp(year=int(year), month=1, day=1)
        train = rows[rows["entry_date"] < cutoff]
        if len(train) < min_train:
            continue
        w = fit_weight(train["m1"], train["m3"], train["entry"])
        t = rows[rows["date"].dt.year == year].copy()
        t["w"] = w
        t["pred_mix"] = mix(t["m1"].to_numpy(float), t["m3"].to_numpy(float), w)
        out.append(t)
    return pd.concat(out, ignore_index=True) if out else rows.iloc[0:0]


# --- dataset ------------------------------------------------------------------

def run_asset(job):
    from zones import analyze, fetch_daily
    from zones.target import compute

    tier, sym = job
    try:
        raw = fetch_daily(sym, years=25).reset_index(drop=True)
    except Exception as e:      # network / delisted: report, keep the others
        return sym, f"ERROR {type(e).__name__}", []
    from band_walkforward import asof_vol

    frame, _ = analyze(raw)
    lo = raw["low"].astype(float).to_numpy()
    hi = raw["high"].astype(float).to_numpy()
    score = frame["score"].to_numpy(float)
    score_raw = frame["score_raw"].to_numpy(float)
    close = raw["close"].to_numpy(float)
    dates = pd.to_datetime(raw["date"], utc=True)
    rows = []
    for i in range(START, len(raw) - 1, STEP):
        res = compute(raw.iloc[:i], sym)
        if res is None:
            continue
        for side, thr in (("buy", BUY_U), ("sell", SELL_U)):
            b = res[side]
            m3 = (b["m3"] or {}).get("mid")
            inside = score[i - 1] <= thr if side == "buy" else score[i - 1] >= thr
            row = {"cls": tier, "sym": sym, "side": side,
                   "date": str(dates.iloc[i - 1].date()), "spot": float(close[i - 1]),
                   "inside": bool(inside), "m1": b["m1"], "m3": m3,
                   "cons": b["consensus"], "fwd_bars": min(H, len(raw) - i),
                   "vol": asof_vol(close[:i]),
                   "fmin": float(np.nanmin(lo[i: i + H])),
                   "fmax": float(np.nanmax(hi[i: i + H]))}
            for tag, sc in (("", score), ("_raw", score_raw)):
                j = first_entry(sc, i, H, thr, side)
                row[f"entry{tag}"] = float(close[j]) if j >= 0 else None
                row[f"entry_date{tag}"] = str(dates.iloc[j].date()) if j >= 0 else None
            rows.append(row)
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

def band_rows(df: pd.DataFrame, target: str, side: str) -> pd.DataFrame:
    """Touches of `target` within the forward window, with the depth the price
    ran into the zone measured from that target (the band's quantity)."""
    d = df[(df["side"] == side) & df[target].notna()]
    if side == "buy":
        d = d[d["fmin"] <= d[target]]
        depth = (d[target] - d["fmin"]) / d[target]
    else:
        d = d[d["fmax"] >= d[target]]
        depth = (d["fmax"] - d[target]) / d[target]
    d = d.assign(depth=depth)
    return d[np.isfinite(d["vol"]) & (d["vol"] > 0)]


def evaluate_band(df: pd.DataFrame) -> dict:
    """Band anchored on the consensus (shipped k) vs on M1 (k refit walk-forward),
    and the full-sample k for M1 (the value to ship if M1 becomes the target).
    Only as-ofs with the full forward window, so `end` is a real date."""
    from band_walkforward import K_BUY, K_SELL, MAX_DEPTH, fit_k, pinball, walkforward

    df = df[df["fwd_bars"] >= H].assign(date=pd.to_datetime(df["date"]))
    df = df.assign(end=df["date"] + pd.Timedelta(days=int(H * 365 / 252)))
    res = {}
    for side, k, cap in (("buy", K_BUY, MAX_DEPTH), ("sell", K_SELL, None)):
        res[side] = {}
        for target in ("cons", "m1"):
            b = band_rows(df, target, side)
            t = walkforward(b, k, cap)
            y = t["depth"].to_numpy(float)
            res[side][target] = {
                "touches": len(b), "n": len(t), "k_full": fit_k(b["depth"], b["vol"]),
                **{c: {"pinball": pinball(t[f"pred_{c}"], y),
                       "coverage": float(np.mean(y <= t[f"pred_{c}"].to_numpy(float)))}
                   for c in ("current", "global")}}
            r = res[side][target]
            print(f"{side:4} target={target:4} touches {r['touches']:4d} OOS n={r['n']:4d} "
                  f"k_full={r['k_full']:.2f}  shipped-k {r['current']['pinball']:.4f} "
                  f"cov{r['current']['coverage']:.2f}  refit-k {r['global']['pinball']:.4f} "
                  f"cov{r['global']['coverage']:.2f}")
    (HERE / "band_results.json").write_text(json.dumps(res, indent=2))
    return res


CANDS = ("m1", "m3", "cons", "mix")


def _err(t: pd.DataFrame, col: str) -> dict:
    e = np.log(t["entry"].to_numpy(float) / t[col].to_numpy(float))
    return {"mae": float(np.mean(np.abs(e))), "mdae": float(np.median(np.abs(e))),
            "bias": float(np.median(e))}


def evaluate(df: pd.DataFrame) -> dict:
    df = df.assign(date=pd.to_datetime(df["date"]))
    res = {}
    for side in ("buy", "sell"):
        for tag in ("", "_raw"):
            d = df[(df["side"] == side) & ~df["inside"].astype(bool)]
            d = d.dropna(subset=["m1", "m3", "cons", f"entry{tag}"])
            d = d.assign(entry=d[f"entry{tag}"], entry_date=pd.to_datetime(d[f"entry_date{tag}"]))
            t = walkforward_mix(d).rename(columns={"pred_mix": "mix"})
            key = f"{side}{tag}"
            res[key] = {"n": len(t), "w_last": float(t["w"].iloc[-1]),
                        "all": {c: _err(t, c) for c in CANDS},
                        "by_class": {k: {c: _err(g, c)["mae"] for c in CANDS}
                                     for k, g in t.groupby("cls")}}
            r = res[key]
            print(f"\n=== {key}  entered {len(d)}  OOS n={r['n']}  w(M1) last={r['w_last']:.2f}")
            print("   " + "  ".join(
                f"{c}: mae {r['all'][c]['mae']:.3f} bias {r['all'][c]['bias']:+.3f}" for c in CANDS))
            for k, v in r["by_class"].items():
                print(f"   {k:8} " + "  ".join(f"{c} {v[c]:.3f}" for c in CANDS))
    (HERE / "results.json").write_text(json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "eval"
    if cmd == "run":
        run()
    elif cmd == "eval":
        evaluate(pd.read_csv(ROWS))
    elif cmd == "band":
        evaluate_band(pd.read_csv(ROWS))
    else:
        sys.exit(f"unknown command: {cmd} (use run | eval | band)")
