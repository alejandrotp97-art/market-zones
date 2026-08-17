"""Agrega multidate_rows.csv y responde: ¿el error del suelo es SESGO o DISPERSIÓN?

SESGO -> mediana lejos de 0 con IQR chico -> se calibra restando un factor.
DISPERSIÓN -> mediana cerca de 0 pero colas gordas -> NO se calibra (romperías
lo que funciona y no atajarías los cracks).

Métrica del suelo: b_over = (mínimo_futuro - objetivo_compra)/objetivo_compra.
Condicional a que el objetivo se ALCANZÓ (b_hit=1), b_over<=0 = cuánto lo perforó.
"""
import numpy as np
import pandas as pd

df = pd.read_csv("multidate_rows.csv")
TIER_ORD = ["index", "mega", "quality", "growth", "small", "crypto"]


def q(a, p):
    return float(np.nanquantile(a, p)) if len(a) else float("nan")


def pct(x):
    return f"{x*100:+.1f}%" if np.isfinite(x) else "  —  "


def floor_stats(sub):
    """(n, hit_rate, median blow-through|hit, IQR|hit, p10 tail|hit, lost%)."""
    n = len(sub)
    hits = sub[sub["b_hit"] == 1]
    ov = hits["b_over"].to_numpy(float)
    ov = ov[np.isfinite(ov)]
    if len(ov) == 0:
        return n, 0.0, np.nan, (np.nan, np.nan), np.nan, np.nan
    med = q(ov, 0.50)
    iqr = (q(ov, 0.25), q(ov, 0.75))
    p10 = q(ov, 0.10)                     # cola: peores perforaciones
    # si bajo el objetivo a la mediana de perforación, ¿qué % de hits actuales se pierden?
    lost = float(np.mean(ov > med))       # los más superficiales que la mediana
    return n, len(ov) / n, med, iqr, p10, lost


def ceil_stats(sub):
    n = len(sub)
    hits = sub[sub["s_hit"] == 1]
    ov = hits["s_over"].to_numpy(float)
    ov = ov[np.isfinite(ov)]
    if len(ov) == 0:
        return n, 0.0, np.nan, (np.nan, np.nan), np.nan
    return (n, len(ov) / n, q(ov, 0.50), (q(ov, 0.25), q(ov, 0.75)), q(ov, 0.90))


print("=" * 92)
print("SUELO (objetivo de COMPRA — leer Capitulación)   |  perforación = cuánto cayó por DEBAJO")
print("=" * 92)
print(f"{'grupo':9} {'n':>5} {'hit%':>6} {'mediana':>9} {'IQR (p25..p75)':>20} "
      f"{'cola p10':>9} {'se pierde si calibro':>20}")
print("-" * 92)


def line(label, sub):
    n, hr, med, iqr, p10, lost = floor_stats(sub)
    print(f"{label:9} {n:>5} {hr*100:>5.0f}% {pct(med):>9} "
          f"{pct(iqr[0])+' .. '+pct(iqr[1]):>20} {pct(p10):>9} "
          f"{(f'{lost*100:.0f}% de hits' if np.isfinite(lost) else '—'):>20}")


line("TODOS", df)
print("-" * 92)
for t in TIER_ORD:
    sub = df[df["tier"] == t]
    if len(sub):
        line(t, sub)

print("\n" + "=" * 92)
print("por activo (suelo)")
print("-" * 92)
print(f"{'sym':8} {'tier':8} {'n':>4} {'hit%':>6} {'med perf':>9} {'IQR':>20} {'p10':>9}")
for sym in df["sym"].drop_duplicates():
    sub = df[df["sym"] == sym]
    n, hr, med, iqr, p10, _ = floor_stats(sub)
    tier = sub["tier"].iloc[0]
    print(f"{sym:8} {tier:8} {n:>4} {hr*100:>5.0f}% {pct(med):>9} "
          f"{pct(iqr[0])+'..'+pct(iqr[1]):>20} {pct(p10):>9}")

print("\n" + "=" * 92)
print("TECHO (objetivo de VENTA — leer Euforia)   |  superación = cuánto subió por ENCIMA")
print("=" * 92)
print(f"{'grupo':9} {'n':>5} {'hit%':>6} {'mediana':>9} {'IQR (p25..p75)':>22} {'cola p90':>9}")
print("-" * 92)
for label, sub in [("TODOS", df)] + [(t, df[df["tier"] == t]) for t in TIER_ORD]:
    if not len(sub):
        continue
    n, hr, med, iqr, p90 = ceil_stats(sub)
    print(f"{label:9} {n:>5} {hr*100:>5.0f}% {pct(med):>9} "
          f"{pct(iqr[0])+' .. '+pct(iqr[1]):>22} {pct(p90):>9}")

# robustez: submuestra NO solapada (1 de cada ~8 as-of por símbolo => ventanas disjuntas)
print("\n" + "=" * 92)
print("ROBUSTEZ suelo — submuestra NO solapada (ventanas de 2a disjuntas)")
sub_rows = []
for sym in df["sym"].drop_duplicates():
    s = df[df["sym"] == sym].reset_index(drop=True)
    sub_rows.append(s.iloc[::8])           # STEP=63, H=504 -> cada 8 no solapa
nol = pd.concat(sub_rows) if sub_rows else df
n, hr, med, iqr, p10, lost = floor_stats(nol)
print(f"  n={n}  hit={hr*100:.0f}%  mediana perf={pct(med)}  "
      f"IQR={pct(iqr[0])}..{pct(iqr[1])}  p10={pct(p10)}")

print("\n" + "=" * 92)
print("VEREDICTO (pooled suelo):")
n, hr, med, iqr, p10, lost = floor_stats(df)
width = iqr[1] - iqr[0]
print(f"  mediana de perforación = {pct(med)}   IQR ancho = {width*100:.0f} pts   "
      f"cola p10 = {pct(p10)}")
print(f"  ratio dispersión/sesgo = {abs(width/med):.1f}x   "
      f"(>>1 = domina la dispersión, NO calibrable por un factor)")
print(f"  calibrar el objetivo a la mediana sacrificaría ~{lost*100:.0f}% "
      f"de las entradas de suelo que hoy disparan.")
