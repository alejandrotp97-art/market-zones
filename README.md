# market-zones

A composite "cheapness" index for a single asset. It scores price on a 0–100
scale and maps it to a buy/sell zone (**Capitulación → Euforia**) — a
re-implementation of the *Panel de Zonas de Mercado* model.

This repo is **the engine (the brain)**. The dashboard is a separate layer that
sits on top of it.

> ### 👉 ¿Sólo quieres instalarlo y usarlo?
>
> **[Lee la GUÍA paso a paso](GUIA.md)** — escrita para quien no programa: cómo
> descargarlo, instalarlo, arrancarlo y meter tu cartera. No hace falta saber
> nada de lo que viene a continuación.
>
> El resto de este README describe el modelo y su implementación, y está
> dirigido a quien vaya a tocar el código.

## The model

Four components, each oriented the same way (**high = expensive/euphoric,
low = cheap/capitulation**) and normalized to 0–100:

| Component | Weight | What it measures |
|-----------|:------:|------------------|
| **Stretch**    | 27.0% | Price vs its MA200 (Mayer Multiple) → z-score → 0–100 |
| **RSI(14)**    | 18.0% | Wilder momentum (already 0–100) |
| **Drawdown**   | 22.5% | Drop from the running all-time high, as a **percentile of the asset's own** drawdown history |
| **TrendDev**   | 22.5% | Residual of `log(price)` vs its log-linear trend → z-score → 0–100 |
| **Volatility** | 10.0% | Annualized realized vol → z-score → 0–100, **inverted**: calm = complacent = high, panic = capitulation = low |

```
score_raw = 0.270·stretch + 0.180·rsi + 0.225·drawdown + 0.225·trend_dev + 0.100·volatility
score     = EMA(7) of score_raw
```

The first four weights are `BASE_FULL` (30/20/25/25) rescaled by `1 - vol_weight`,
so changing `vol_weight` preserves their relative balance and the total stays 1.0.

The four extra "obvious" candidates (MACD, %B, stochastic, ROC) were rejected by
measurement — each is 0.61–0.92 correlated with a component already present, so
they add no new dimension. Volatility (max |ρ| 0.46) and volume (0.25) were the
only orthogonal additions. Volatility is orientable onto the cheap↔expensive
axis, so it joins the score; volume is not (it spikes at BOTH extremes), so it
stays out — see the conviction layer.

### Conviction layer (confirmation, not a score input)

Volatility **and** volume grade an *extreme* zone by how climactic it is,
without moving the score. A Capitulación with a volatility spike + a volume
climax is a **high-conviction (confirmed)** bottom; a Capitulación in calm, thin
tape is **unconfirmed** (a slow bleed — wait). Shown as a chip on the zone:
`Clímax confirmado` / `Parcial` / `Sin confirmar`. Continuous futures (`=F`)
drop volume (rollover noise) and fall back to volatility only.

### Score → zone (with 2-point hysteresis)

| Zone | Range |
|------|-------|
| Capitulación | [0, 20) |
| Acumulación  | [20, 40) |
| Equilibrio   | [40, 60) |
| Precaución   | [60, 80) |
| Euforia      | [80, 100] |

Hysteresis of 2 points: the zone does not flip at an exact boundary — the score
must overshoot by 2 to switch, which stops flickering.

### Model selection by history length

Causal normalization needs a burn-in **on top of** the indicator's own window
(MA200, then a year of Mayer values before the first z-score), so the
thresholds differ between the two paths:

| Rows | `causal=True` (default) | `causal=False` (legacy) |
|------|-------------------------|-------------------------|
| ≥ 452 | full model | full model |
| 200–451 | reduced model | full model |
| 50–199 | reduced model | reduced model |
| 30–49 | no zone | reduced model |
| < 30 | no zone | no zone |

An asset with 250 rows cannot support a causal MA200 model — there is no honest
way to rank today's Mayer multiple against a history that does not exist yet, so
it drops to the reduced model instead of printing an all-NaN score.

## ⚠️ Causality: the chart shows what the index SAID, not what it would say now

Every component is normalized against **its own past only** (`[0..t]`), so a
point on the chart never moves once printed. This is `causal=True`, the default.

The legacy path (`causal=False`, kept only for A/B) z-scores and percentile-ranks
against the **whole series**, including data that did not exist yet on the date
being plotted. That curve is revisionist, and not slightly — measured over 25 y:

| Symbol | mean \|Δscore\| | max \|Δscore\| | days the chart showed a **different zone** |
|--------|---------:|--------:|-----:|
| SPY  |  9.5 | 30.5 | 44.8% |
| GLD  | 10.8 | 30.2 | 55.0% |
| NLR  | 10.3 | 17.6 | 55.7% |
| 0P0000CV2T.F | 18.0 | 45.1 | 58.1% |

Switching to causal does **not** move today's reading: at `t = n-1` the expanding
window IS the whole series, so Stretch, Drawdown and Volatility come out
bit-identical (asserted in `tests/test_causal.py`). Only TrendDev shifts, because
its trend line was previously fitted with knowledge of where the price ended.

**The normalization window still matters.** The engine normalizes over whatever
frame it is given — choosing it is the caller's job (CLI `--since`, or the
dashboard's range). Same asset (NLR), same code, different `--since`:

| Window | Score | Zone |
|--------|:-----:|------|
| 2007→ (19 y) | 54.8 | Equilibrio |
| **2023-02→** | **9.0** | **Capitulación** |

## Usage

```bash
PY=./.venv/bin/python                          # any interpreter with numpy+pandas

$PY cli.py NLR                      # full history
$PY cli.py NLR --since 2023-02-03   # reproduce the dashboard window
$PY cli.py URNM --tail 10
```

Programmatic:

```python
from zones import analyze, fetch_daily
df = fetch_daily("NLR", years=25)
frame, summary = analyze(df)          # frame has per-date components for tooltips
print(summary.zone_name, summary.score)
```

## Architecture

Two pure cores with zero I/O, each fully unit-tested without the network, and a
web layer on top that owns every socket in the project.

**The scoring engine** — what a price is worth relative to its own history:

```
zones/normalize.py    z_to_100, pct_rank + expanding_* (causal twins)
zones/indicators.py   stretch, rsi, drawdown, trend_dev, volatility, ema
zones/classify.py     zones + hysteresis + verdict text
zones/conviction.py   the climax layer (grades an extreme, never scores it)
zones/engine.py       model selection → compose → EMA → classify → Summary
zones/resample.py     daily → weekly bars, so one engine scores both
zones/target.py       price inversion: at what price would TODAY read this zone
zones/data.py         Yahoo daily OHLCV via urllib   (the only network module)
```

**The portfolio domain** — what a movement means. Added later than the engine
and, for a long time, the half that did *not* follow this architecture: it lived
inside `dashboard.py` between the Flask routes and the SQL. It does now.

```
cartera/parsing.py    text → data: numbers, dates, buy/sell/dividend, ISIN,
                      instrument type, commercial name. No network, no disk,
                      no clock.
cartera/positions.py  movements → valued positions. The money arithmetic.
                      Takes the market as a parameter — six methods — so the
                      rule that cost basis uses the fx of the PURCHASE date can
                      be verified without reaching Yahoo.
cartera/returns.py    TWR, XIRR, drawdown, volatility, Sharpe, effective N,
                      currency decomposition, buy-only rebalancing. Pure
                      functions over a series of values and flows.
cartera/plan.py       the investor's own plan: contribution calendar, goal
                      progress, and the attention rules. Today's date arrives
                      as a parameter so a test can stand on any day.
cartera/splits.py     splits: detect, preview, and the invariant that makes
                      adjusting safe — quantity x ratio, price / ratio, total
                      cost unchanged.
cartera/exposure.py   currency exposure, two readings: quotation currency
                      (exact, uninformative) and economic currency, derived
                      from the same country look-through the map uses.
cartera/fiscal.py     what a sale would leave: non-destructive FIFO preview,
                      progressive savings-base tax, loss offsetting, and the
                      two-month repurchase rule. Brackets are a parameter, not
                      a constant in the maths.
```

### Two returns, because there are two questions

| | Answers | Moved by when you contributed |
|---|---|---|
| **TWR** | How did the assets I picked do? | No — the only figure comparable to an index |
| **XIRR** | What did *I* actually get? | Yes |

The gap between them **is** the effect of the contribution schedule — the one
number in this panel that grades a decision of the investor rather than one of
the market.

Three details decide whether these come out right:

- **Dividends enter as withdrawals.** A distribution drops the price without
  anything being lost; subtracting it as an outflow cancels exactly that drop.
  Without it, every payout would read as a loss.
- **Full daily resolution.** The chart downsamples to ~800 points for drawing;
  chaining sub-periods over one day in four puts flows on the wrong day.
- **The benchmark comparison uses the portfolio's price-only TWR.**
  `zones/data.py` requests Yahoo's raw close, not `adjclose`, so the index
  series carries no dividends either. Comparing total return against price
  return hands the reader the index's dividend yield.

XIRR is solved by bisection, not Newton-Raphson. Speed is irrelevant here and
Newton can wander off a shallow root — which is exactly the shape of a
portfolio built from small monthly contributions.

### Windowing is not slicing

The chart's benchmark line is seeded at the portfolio's **first movement** and
fed the portfolio's own cashflows. Slicing that array to show three months
would compare three months of portfolio against an index position opened two
years ago — most of the visible gap would be inherited history.

A window therefore **rebases**: the index is re-seeded on the window's first
day with the portfolio's value that day and receives only the flows inside the
window; `invested` becomes the capital already at risk plus contributions made
inside. All three lines start from the same point, which is what choosing a
range implies.

The window's first point is the last close **strictly before** the period
starts — measuring January needs December 31st's close. `side="left"` matters:
when the start date falls on a trading day, `"right"` lands *on* it and the
first day of the period has nothing to measure against.

For XIRR over a window, the capital already held on day one is entered as a
purchase on that day. Without it there would be inflows with no outflow paying
for them, and the rate would diverge.

### Drawdown is measured on the return index, never on the balance

A contribution raises the euro value, and contributing is not recovering. A
transfer can push the balance back to its previous high and make a drawdown the
market never recovered from look resolved — with monthly contributions, a long
slide may never appear as a drawdown at all. So `nav_series` chains the same
TWR factors into "what one euro invested is worth", and `drawdown` runs on
that. Same reason for volatility: the jump on a contribution day is not a
market move, and a portfolio funded monthly would read as far more volatile
than it is.

### Exact and estimated must not share a number

The sale simulator splits its answer in two, and the split is the feature. The
FIFO lots consumed, their cost, the proceeds and the resulting gain all derive
from movements already recorded — verifiable. The tax is an estimate that
depends on a law that changes and on facts the app cannot see: the rest of the
year's savings income, losses carried from earlier years, and whether the user
files in a foral territory. Folding both into one figure would dress an
estimate as a datum.

Two details that are easy to get wrong:

- **A gain is not taxed at the lowest bracket.** `tax_on_gain` returns the
  *difference* between the liability with and without the gain, given what has
  already been realized that year. Multiplying the gain by 19% is the classic
  error.
- **This year's realized gains are derived by running the same accounting
  twice** — the whole book, and the book up to 31 December — and subtracting.
  Reimplementing a per-year FIFO would be a second copy of the most delicate
  rule in the project, and two copies drift apart.

### Beta is never published without its correlation

A beta of 1.2 alongside a correlation of 0.3 does not mean "moves 20% more than
the index" — it means the index explains very little of what the portfolio does
and that 1.2 is mostly noise. `beta()` returns both plus the sample size, and
the UI picks its wording from the correlation.

It runs on the return index, not the euro balance, for the same reason drawdown
does. And the daily pairs are built by walking both series together: computing
each side's returns independently and zipping them would pair one series'
Monday with the other's Tuesday whenever one had a flat day the other did not —
a misalignment that produces a plausible number and no error.

### Effective number of bets

```
by weights        1 / SUM(w_i^2)
by correlation    1 / SUM_ij(w_i w_j rho_ij)
```

The second generalizes the first: with the identity matrix it returns the first
exactly; with everything correlated at 1 it returns 1. Counting lines is not
diversification — five funds tracking the same index are one bet spread over
five rows, and `1/SUM(w^2)` says "5" for both cases.

Three movement types, and only two of them move the position:

| Movement | Quantity | Cost basis | Where it lands |
|----------|----------|------------|----------------|
| buy      | up       | up         | `invested` |
| sell     | down     | down       | `realized` **and** `realized_fifo` |
| dividend | —        | —          | `income`, its own column |

A dividend folded into the cost basis would lower the average price (a tax
convention of a different country) and folded into realized P&L would mix with
capital gains, which are taxed under their own rule. It goes to `income`, adds
to total return, and touches nothing else.

**Realized P&L is reported twice on purpose.** `realized` uses weighted average
cost — how a portfolio reads. `realized_fifo` uses first-in-first-out, the
criterion Spanish law applies to fungible securities. They are identical
whenever a position is closed in full (both consume every lot) and can only
diverge on a PARTIAL sale. That divergence is exactly the gap between the number
an app shows and the number a tax return wants, so both are published rather
than picking one and calling it "the result".

**Everything else**, none of it imported by the two cores above:

```
dashboard.py          Flask: routes, caches, rate limit, CSRF, host guard,
                      SQLite persistence, the Yahoo adapters
cli.py                command-line entrypoint (no server needed)
backup_cartera.py     SQLite online backup of the movement book + rotation
alertas_cartera.py    daily zone-change watch over the open positions. Notifies
                      on entering or leaving an extreme zone only — a state, not
                      a forecast; it never says buy or sell
geo.py                country look-through of funds and ETFs
regime/               market-regime panel (its own package and suite)
validation/           strict-causal walk-forward of the regime predictions
research/             the v2 experimental program (E1–E10)
analysis/             redundancy study and PNG reports
snapshot.py           self-contained static HTML export
```

`zones/data.py` raises **`NoHistory`** when Yahoo answers with a populated `meta`
but no `timestamp` array — "priceable but not chartable", seen on ISIN-style
European listings (`IE000M7V94E1.SG`). It has its own exception type because
callers must be able to tell it apart from "ticker does not exist": conflating
the two is how a holding silently disappears from a chart while its cost stays in.

## Tests

483 tests, hermetic — every outbound call is stubbed, so the suite never touches
the network and never depends on Yahoo being up.

```bash
pytest                                  # everything (tests/ + regime/tests/)
pytest tests/test_causal.py             # the no-lookahead guarantee
pytest tests/test_positions_domain.py   # the money arithmetic, no Flask, no net
pytest tests/test_daily_golden.py       # the score is byte-identical to the golden
pytest tests/test_cartera_dividendos_edicion.py   # dividends, editing, per-position zone
pytest tests/test_returns.py            # TWR/XIRR, checked against Excel's published example
```

CI runs `ruff check` before the suite — a name that does not exist is reported
in two seconds with file and line, rather than surfacing as an import error
halfway through the tests. The rule set is curated and carries zero exceptions,
so a new warning always means something is actually wrong.

CI runs the same suite on every push and once a week on Python 3.11, 3.12 and
3.14 — the floor the guide promises, and the one production actually serves on —
installing `requirements.txt` exactly as `GUIA.md` tells a reader to. The weekly
run is the one that matters: nothing here changes for months, but numpy and
pandas do. `constraints.txt` records the exact versions production runs on, so
"did I break it or did pandas?" is an answerable question.

**The panel has its own virtualenv** (`.venv/`, Python 3.14) since 2026-08-19.
It used to borrow `btc-poly/.venv`, shared by 105 systemd units — several of
them live trading bots — which meant a `pip install` here changed the ground
under them, and the interpreter could never move. It can now, and that shared
venv is left alone.

**3.10 is not supported, and the CI is what found out.** `GUIA.md` promised
"3.10 or higher" and nobody had ever checked. pandas 3 requires Python >= 3.11,
so on 3.10 pip installs pandas 2.3.3, which labels NaN differently and defaults
datetimes to nanosecond rather than second resolution — the goldens, generated
under the pandas 3 that production runs, do not match. The first run of this
workflow said so.

Hermeticity is enforced by a socket guard in `conftest.py`, gated on
`MZ_NO_NETWORK=1`, not by a firewall rule. The original step ran
`sudo iptables -P OUTPUT DROP` and re-ran the suite; that also severs the
runner's own link to GitHub, so the job cannot report and hangs until timeout —
45 minutes for a 27-second suite, with no message saying why.
