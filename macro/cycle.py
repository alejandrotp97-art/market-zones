"""Fase del ciclo económico: el reloj de la OCDE corregido por el estrés.

EL RELOJ
--------
El CLI de la OCDE está ajustado en amplitud: 100 es la tendencia de largo plazo.
Dos preguntas bastan para situar la economía:

    ¿por encima de su tendencia?  ×  ¿subiendo?
    sí × sí → Auge          sí × no → Recesión (desaceleración desde arriba)
    no × no → Crisis        no × sí → Recuperación

Cada respuesta es una probabilidad (una logística sobre la distancia a 100 y
sobre el impulso de 3 meses), no un sí/no: un CLI en 100,02 no está «en auge»
con la misma convicción que uno en 101.

CRISIS NO ES «DEBAJO Y CAYENDO»
-------------------------------
El reloj mide el ciclo de CRECIMIENTO, no el de negocio: crecer por debajo de la
tendencia no es una crisis. Validado contra las recesiones del NBER (2000-2026),
el cuadrante «debajo y cayendo» a secas marcaba Crisis en 78 meses de expansión
(2012, 2016, 2023). Por eso ese cuadrante es Recesión (contracción) y sólo se
vuelve Crisis en la proporción de su severidad: alarmas graves y un compuesto
de pilares hundido. Con eso quedan 24 meses —2002, 2011, el COVID—, todos de
estrés real, y los 28 meses de recesión del NBER siguen cayendo en Crisis o
Recesión.

EL ESTRÉS
---------
El CLI es lento y se revisa. Los mercados de crédito, la volatilidad y el empleo
avisan antes de que el CLI cruce 100. Cada alarma GRAVE traslada un 20 % de la
probabilidad un paso hacia la crisis (Auge→Recesión, Recuperación→Crisis,
Recesión→Crisis), hasta un 75 %; un compuesto de pilares muy negativo suma su
parte. Las alarmas leves (curva invertida, peticiones de subsidio al alza) se
enseñan, pero no mueven la fase: avisan con meses o años de adelanto y moverla
con ellas sería anunciar crisis demasiado pronto.

Nada de esto es una recomendación de inversión: es una lectura del estado de
la economía de EE. UU. con datos públicos y revisados.
"""
from __future__ import annotations

import calendar
import math

from . import pillars as P
from .sources import Series

PHASES = [
    ("auge", "Auge"),
    ("recesion", "Recesión"),
    ("crisis", "Crisis"),
    ("recuperacion", "Recuperación"),
]
PHASE_KEYS = [k for k, _ in PHASES]
LEVEL_SCALE, MOMENTUM_SCALE = 0.3, 0.05      # pts sobre 100 · pts por mes
STEP_PER_ALARM, MAX_STEP = 0.20, 0.75
SEVERITY_PER_ALARM = 0.5
TRAIL_MONTHS = 24

# (indicador, umbral, es_grave, condición, texto)
ALARM_RULES = [
    ("sahm", 0.5, True, ">=", "Regla de Sahm activada: el paro sube como al empezar una recesión"),
    ("hy", 6.0, True, ">=", "Diferencial high yield por encima del 6 %"),
    ("hy_chg", 1.5, True, ">=", "El high yield se ha ensanchado más de 1,5 pp en 3 meses"),
    ("nfci", 0.0, True, ">", "Condiciones financieras más duras que la media (NFCI > 0)"),
    ("stlfsi", 1.0, True, ">", "Estrés financiero alto (St. Louis > 1)"),
    ("vix", 30.0, True, ">=", "VIX en zona de pánico (≥ 30)"),
    ("spx_dd", -20.0, True, "<=", "S&P 500 en mercado bajista (−20 % desde máximos)"),
    ("curve", 0.0, False, "<", "Curva 10 años − 3 meses invertida"),
    ("claims", 20.0, False, ">=", "Peticiones de subsidio un 20 % por encima de hace un año"),
    ("epu", 250.0, False, ">=", "Incertidumbre en prensa muy alta (EPU ≥ 250)"),
]


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-max(-50.0, min(50.0, x))))     # acotado: sin OverflowError


def base_probs(level: float, momentum: float) -> dict[str, float]:
    above, rising = _sig((level - 100) / LEVEL_SCALE), _sig(momentum / MOMENTUM_SCALE)
    return {"auge": above * rising, "recesion": above * (1 - rising),
            "crisis": (1 - above) * (1 - rising), "recuperacion": (1 - above) * rising}


def apply_stress(probs: dict[str, float], alarms: list[dict], composite: float | None) -> dict[str, float]:
    severe = sum(1 for a in alarms if a["severe"])
    f = STEP_PER_ALARM * severe + max(0.0, -(composite or 0.0) - 0.3)
    f = min(MAX_STEP, f)
    if f <= 0:
        return dict(probs)
    p = dict(probs)
    move = {"auge": "recesion", "recuperacion": "crisis", "recesion": "crisis"}
    delta = {k: probs[k] * f for k in move}
    for src, dst in move.items():
        p[src] -= delta[src]
        p[dst] += delta[src]
    return p


def severity(alarms: list[dict], composite: float | None) -> float:
    """0 = contracción tranquila · 1 = crisis: alarmas graves y pilares hundidos."""
    graves = sum(1 for a in alarms if a["severe"])
    return min(1.0, SEVERITY_PER_ALARM * graves + 2 * max(0.0, -(composite or 0.0) - 0.1))


def final_probs(base: dict[str, float], alarms: list[dict], composite: float | None) -> dict[str, float]:
    q = dict(base)
    s = severity(alarms, composite)
    q["recesion"] += q["crisis"] * (1 - s)
    q["crisis"] *= s
    return apply_stress(q, alarms, composite)


def alarms(evaluation: dict) -> list[dict]:
    vals = {i["key"]: i["value"] for pil in evaluation["pillars"] for i in pil["indicators"]}
    out = []
    for key, thr, severe, op, text in ALARM_RULES:
        v = vals.get(key)
        if v is None:
            continue
        hit = {">=": v >= thr, ">": v > thr, "<": v < thr, "<=": v <= thr}[op]
        if hit:
            out.append({"key": key, "severe": severe, "value": v, "threshold": thr, "text": text})
    return out


def clock_point(cli: Series, date: str) -> dict | None:
    ref = P._shift(date, P.PUB_LAG["CLI:USA"])
    got = P.value_at(cli, ref, max_age_days=100)
    if not got:
        return None
    # Serie mensual: el impulso se mide contra el dato de hace TRES observaciones
    # (o las que haya). Restar 91 días caería en el mes de antes en marzo-junio.
    i = P._upto(cli, got[0]) - 1
    k = min(3, i)
    if k < 1:
        return None
    return {"date": got[0], "level": got[1], "momentum": (got[1] - cli[i - k][1]) / k}


def month_end(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def _months(start: str, end: str) -> list[str]:
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _read(full: dict[str, Series], date: str, area: str = "USA") -> dict:
    ev = P.evaluate(full, date, derived=True)
    al = alarms(ev)
    pt = clock_point(full.get(f"CLI:{area}") or [], date)
    probs = final_probs(base_probs(pt["level"], pt["momentum"]), al, ev["composite"]) if pt else None
    return {"evaluation": ev, "alarms": al, "clock": pt, "probs": probs,
            "phase": max(probs, key=probs.get) if probs else None}


def history(data: dict[str, Series], start: str = "2000-06", end: str | None = None,
            derived: bool = False) -> list[dict]:
    full = data if derived else P.derive(data)
    end = end or P.latest_date(data)[:7]
    out = []
    for month in _months(start, end):
        r = _read(full, month_end(month))
        pt = r["clock"]
        out.append({"month": month, "phase": r["phase"],
                    "clock_date": pt["date"] if pt else None,
                    "level": pt["level"] if pt else None,
                    "momentum": pt["momentum"] if pt else None,
                    "composite": r["evaluation"]["composite"],
                    "severe": sum(a["severe"] for a in r["alarms"])})
    return out


def classify(data: dict[str, Series], date: str | None = None, start: str = "2000-06") -> dict:
    full = P.derive(data)
    date = date or P.latest_date(data)
    now = _read(full, date)
    hist = history(full, start=start, end=date[:7], derived=True)
    trail = [{"month": h["month"], "level": h["level"], "momentum": h["momentum"], "phase": h["phase"]}
             for h in hist if h["level"] is not None][-TRAIL_MONTHS:]
    g20 = clock_point(full.get("CLI:G20") or [], date)
    probs = now["probs"]
    return {
        "as_of": date,
        "phase": now["phase"],
        "confidence": max(probs.values()) if probs else None,
        "probs": probs,
        "clock": now["clock"],
        "clock_g20": g20,
        # El G20 sólo tiene reloj: los pilares de estrés son de EE. UU.
        "phase_g20": max((q := final_probs(base_probs(g20["level"], g20["momentum"]), [], None)), key=q.get)
        if g20 else None,
        "alarms": now["alarms"],
        "pillars": now["evaluation"]["pillars"],
        "composite": now["evaluation"]["composite"],
        "trail": trail,
        # La onda de la tarjeta: CLI − 100 mes a mes con su fase.
        "history": [{"month": h["month"], "phase": h["phase"], "composite": h["composite"],
                     "level": h["level"], "momentum": h["momentum"]} for h in hist],
    }
