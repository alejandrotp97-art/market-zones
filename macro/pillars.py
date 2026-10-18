"""Pilares del ciclo: indicadores macro, de condiciones financieras y de riesgo.

Cada indicador es una transformación de una serie (nivel, variación interanual,
distancia a su media...) que se puntúa en [-1, 1] con `tanh`: 0 en su valor
neutral, positivo cuando el dato es el de una economía en expansión y negativo
cuando es el de una que se enfría o se estresa. La escala dice cuánto tiene que
alejarse el dato del neutral para valer ±0,76 (tanh 1).

Los pilares promedian sus indicadores con dato; un pilar sin ninguno queda FUERA
y su peso se reparte entre los demás. Contarlo como cero sería inventarse que
ese pilar está neutral.

Sustitutos de fuentes que no son públicas (ver `note` de cada indicador):
- The Conference Board LEI → CLI de la OCDE (pilar «adelantados»).
- The Conference Board CEI → sus cuatro componentes en FRED: empleo no agrícola,
  producción industrial, renta real sin transferencias y ventas reales.
- CME FedWatch → lo que descuentan los tipos de mercado: bono a 2 años menos el
  tipo efectivo de la Fed (positivo = el mercado espera subidas).
"""
from __future__ import annotations

import bisect
import datetime as dt
import math
from dataclasses import dataclass

from .sources import Series


@dataclass(frozen=True)
class Indicator:
    key: str
    label: str
    pillar: str
    series: str            # id de FRED o derivada (CLI:USA, DGS2-DFF, REAL_RATE)
    transform: str         # level | yoy | chg_3m | chg_12m | pct_ma200 | dd_52w | avg4_yoy
    neutral: float
    scale: float
    direction: int         # +1: más es mejor para el ciclo; -1: más es peor
    unit: str
    max_age_days: int      # más viejo que esto en la fecha evaluada = sin dato
    note: str = ""


PILLARS = [
    ("adelantados", "Indicadores adelantados", 0.25),
    ("actividad", "Actividad coincidente", 0.20),
    ("empleo", "Mercado laboral", 0.15),
    ("credito", "Crédito y condiciones financieras", 0.15),
    ("monetaria", "Política monetaria y curva", 0.10),
    ("riesgo", "Riesgo de mercado", 0.10),
    ("inflacion", "Inflación", 0.05),
]
M, W, D = 100, 21, 10      # caducidad: mensual (fechado el día 1 y publicado ~45 d después), semanal, diaria

INDICATORS = [
    Indicator("cli_usa", "CLI OCDE EE. UU. (vs 100)", "adelantados", "CLI:USA", "level", 100, 0.5, 1, "pts", M,
              "sustituto del LEI de The Conference Board"),
    Indicator("cli_usa_mom", "CLI EE. UU., cambio 3 meses", "adelantados", "CLI:USA", "chg_3m", 0, 0.3, 1, "pts", M),
    Indicator("cli_g20", "CLI OCDE G20 (vs 100)", "adelantados", "CLI:G20", "level", 100, 0.5, 1, "pts", M),
    Indicator("payems", "Empleo no agrícola, interanual", "actividad", "PAYEMS", "yoy", 1.0, 1.0, 1, "%", M,
              "componente del CEI"),
    Indicator("indpro", "Producción industrial, interanual", "actividad", "INDPRO", "yoy", 0.5, 2.5, 1, "%", M,
              "componente del CEI"),
    Indicator("income", "Renta real sin transferencias, interanual", "actividad", "W875RX1", "yoy",
              1.5, 1.5, 1, "%", M, "componente del CEI"),
    Indicator("sales", "Ventas minoristas reales, interanual", "actividad", "RRSFS", "yoy", 1.0, 3.0, 1, "%", M,
              "componente del CEI"),
    Indicator("sahm", "Regla de Sahm", "empleo", "SAHMREALTIME", "level", 0.3, 0.2, -1, "pp", M,
              "≥ 0,5 ha marcado el inicio de todas las recesiones desde 1970"),
    Indicator("unrate", "Paro, cambio en 12 meses", "empleo", "UNRATE", "chg_12m", 0.2, 0.4, -1, "pp", M),
    Indicator("claims", "Peticiones de subsidio (media 4 sem.), interanual", "empleo", "ICSA", "avg4_yoy",
              5, 10, -1, "%", W),
    Indicator("hy", "Diferencial high yield (OAS)", "credito", "BAMLH0A0HYM2", "level", 4.0, 1.5, -1, "pp", D),
    Indicator("hy_chg", "High yield, cambio 3 meses", "credito", "BAMLH0A0HYM2", "chg_3m", 0, 0.75, -1, "pp", D),
    Indicator("ig", "Diferencial grado de inversión (OAS)", "credito", "BAMLC0A0CM", "level", 1.3, 0.5, -1, "pp", D),
    Indicator("nfci", "Condiciones financieras (NFCI Chicago)", "credito", "NFCI", "level", -0.3, 0.4, -1, "", W,
              "> 0: condiciones más duras que la media"),
    Indicator("stlfsi", "Estrés financiero (St. Louis)", "credito", "STLFSI4", "level", 0, 1.0, -1, "", W),
    Indicator("curve", "Curva 10 años − 3 meses", "monetaria", "T10Y3M", "level", 0.5, 1.0, 1, "pp", D,
              "invertida (< 0) precede a las recesiones"),
    Indicator("fedpath", "2 años − tipo Fed (lo que descuenta el mercado)", "monetaria", "DGS2-DFF", "level",
              0, 0.5, 1, "pp", D, "sustituto de CME FedWatch: < 0 = el mercado descuenta bajadas"),
    Indicator("real_rate", "Tipo Fed real (menos IPC)", "monetaria", "REAL_RATE", "level", 1.0, 1.5, -1, "pp", M),
    Indicator("vix", "VIX", "riesgo", "VIXCLS", "level", 18, 8, -1, "", D),
    Indicator("spx_ma", "S&P 500 vs su media de 200 sesiones", "riesgo", "SP500", "pct_ma200", 0, 8, 1, "%", D),
    Indicator("spx_dd", "S&P 500, caída desde máximo de 52 sem.", "riesgo", "SP500", "dd_52w", -5, 10, 1, "%", D),
    Indicator("cpi", "IPC, interanual", "inflacion", "CPIAUCSL", "yoy", 2.5, 1.5, -1, "%", M),
    Indicator("core_pce", "PCE subyacente, interanual", "inflacion", "PCEPILFE", "yoy", 2.3, 1.0, -1, "%", M),
]
BY_KEY = {i.key: i for i in INDICATORS}
FRED_IDS = sorted({i.series for i in INDICATORS if ":" not in i.series and "-" not in i.series
                   and i.series != "REAL_RATE"} | {"DGS2", "DFF"})


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def _shift(date: str, days: int) -> str:
    return (_d(date) - dt.timedelta(days=days)).isoformat()


def _upto(s: Series, date: str) -> int:
    """Índice del primer dato posterior a `date` (los datos [0, i) son ≤ date)."""
    return bisect.bisect_right(s, (date, math.inf))


def value_at(s: Series, date: str, max_age_days: int | None = None) -> tuple[str, float] | None:
    i = _upto(s, date)
    if i == 0:
        return None
    d, v = s[i - 1]
    if max_age_days is not None and (_d(date) - _d(d)).days > max_age_days:
        return None
    return d, v


def _ago(s: Series, date: str, days: int) -> float | None:
    # El dato de referencia tiene que estar cerca de la fecha buscada: con un
    # hueco de meses la «variación anual» compararía con cualquier cosa.
    got = value_at(s, _shift(date, days), max_age_days=45)
    return got[1] if got else None


def transform(s: Series, kind: str, date: str) -> float | None:
    cur = value_at(s, date)
    if cur is None:
        return None
    d, v = cur
    if kind == "level":
        return v
    if kind in ("yoy", "chg_12m", "chg_3m"):
        prev = _ago(s, d, 91 if kind == "chg_3m" else 365)
        if prev is None:
            return None
        if kind == "yoy":
            return (v / prev - 1) * 100 if prev else None
        return v - prev
    i = _upto(s, d)
    if kind == "pct_ma200":
        win = [x for _, x in s[max(0, i - 200):i]]
        return (v / (sum(win) / len(win)) - 1) * 100
    if kind == "dd_52w":
        lo = bisect.bisect_left(s, (_shift(d, 365),))
        return (v / max(x for _, x in s[lo:i]) - 1) * 100
    if kind == "avg4_yoy":
        j = _upto(s, _shift(d, 364))
        if j < 4:
            return None
        now = sum(x for _, x in s[i - 4:i]) / 4
        then = sum(x for _, x in s[j - 4:j]) / 4
        return (now / then - 1) * 100
    raise ValueError(kind)


def spread(a: Series, b: Series) -> Series:
    """a − b en cada fecha de `a`, con el último dato de `b` a esa fecha."""
    out = []
    for d, v in a:
        got = value_at(b, d, max_age_days=10)
        if got:
            out.append((d, round(v - got[1], 10)))
    return out


def derive(data: dict[str, Series]) -> dict[str, Series]:
    out = dict(data)
    if "DGS2" in data and "DFF" in data:
        out["DGS2-DFF"] = spread(data["DGS2"], data["DFF"])
    if "DFF" in data and "CPIAUCSL" in data:
        cpi = data["CPIAUCSL"]
        yoy = [(d, y) for d, _ in cpi if (y := transform(cpi, "yoy", d)) is not None]
        # El IPC sale a mes vencido: se resta el último publicado a cada fin de mes.
        out["REAL_RATE"] = [(d, v - got[1]) for d, v in data["DFF"]
                            if d.endswith("-01") and (got := value_at(yoy, d, max_age_days=70))]
    return out


def score(ind: Indicator, x: float) -> float:
    return math.tanh(ind.direction * (x - ind.neutral) / ind.scale)


def latest_date(data: dict[str, Series]) -> str:
    return max(s[-1][0] for s in data.values() if s)


def evaluate(data: dict[str, Series], date: str | None = None, derived: bool = False) -> dict:
    """Puntúa todos los indicadores y pilares tal como estaban en `date`."""
    full = data if derived else derive(data)
    date = date or latest_date(data)
    pillars = []
    for key, label, weight in PILLARS:
        rows = []
        for ind in (i for i in INDICATORS if i.pillar == key):
            s = full.get(ind.series) or []
            got = value_at(s, date, ind.max_age_days)
            x = transform(s, ind.transform, got[0]) if got else None
            rows.append({"key": ind.key, "label": ind.label, "unit": ind.unit, "note": ind.note,
                         "source": ind.series, "value": x, "as_of": got[0] if got else None,
                         "score": None if x is None else score(ind, x)})
        vals = [r["score"] for r in rows if r["score"] is not None]
        pillars.append({"key": key, "label": label, "weight": weight, "indicators": rows,
                        "score": sum(vals) / len(vals) if vals else None})
    alive = [p for p in pillars if p["score"] is not None]
    wsum = sum(p["weight"] for p in alive)
    composite = sum(p["weight"] * p["score"] for p in alive) / wsum if wsum else None
    return {"as_of": date, "pillars": pillars, "composite": composite}
