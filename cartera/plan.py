"""El plan de quien invierte: aportaciones, objetivo propio y qué merece atención.

Puro. Entra un puñado de hechos ya calculados, sale una lista de cosas que
decir. Ni red, ni disco, ni reloj — la fecha de hoy se pasa por parámetro, para
que un test pueda situarse en cualquier día sin trucar el sistema.

La diferencia con el resto del paquete: `positions.py` y `returns.py` responden
«¿qué ha hecho el mercado con mi dinero?». Esto responde «¿voy por donde
quería?», que es una pregunta sobre la persona y no sobre el mercado. Por eso
todo lo de aquí necesita que alguien haya declarado algo antes —un peso
objetivo, un capital al que llegar— y por eso, cuando no lo ha declarado, la
respuesta correcta es callarse y no suponer.

LA REGLA QUE GOBIERNA «QUÉ MERECE TU ATENCIÓN»
----------------------------------------------
Un aviso describe un HECHO sobre la cartera y nunca una orden. La diferencia no
es de tono, es de contenido:

    «URNM pesa 8 puntos más que tu objetivo»          hecho
    «Deberías vender URNM»                            orden

El primero se puede comprobar; el segundo requiere saber cosas que este
programa no sabe —cuándo necesitas el dinero, qué impuestos pagarías, qué
piensas del activo—. Y hay una razón medida para no darlo: en este proyecto el
buy & hold le gana a cualquier regla de entrada y salida que se ha probado.

Cada aviso lleva cuatro piezas, y si falta alguna es que el aviso no está
terminado:

    qué pasa · a qué parte de la cartera afecta · por qué importa · qué falta
"""
from __future__ import annotations

import math

# ── umbrales ──────────────────────────────────────────────────────────────
# Un peso nunca cae exacto en su objetivo, así que sin banda muerta el panel
# avisaría todos los días de todo y dejaría de leerse. Cinco puntos es lo que
# el mercado mueve solo en unos meses sin que nadie haga nada.
DRIFT_PP = 5.0
# Concentración: NO basta un umbral fijo. En una cartera de dos posiciones,
# cualquier reparto deja las dos por encima de un tercio, y avisar de que un
# 50/50 está concentrado es ruido — ese reparto es una decisión, no un descuido.
# La regla mira el peso frente a lo que daría un reparto IGUAL entre las
# posiciones que hay: se avisa cuando una pesa el doble de lo que le tocaría, y
# además supera un suelo absoluto.
CONCENTRACION_PCT = 33.0
CONCENTRACION_VECES = 2.0
# Por debajo de dos apuestas independientes, hablar de cartera diversificada es
# un decir.
EFF_N_MIN = 2.0
MESES_SIN_APORTAR = 3
COBERTURA_BAJA_PCT = 90.0

AVISO, INFO = "warn", "info"


def _pct(parte, total):
    return (parte / total * 100.0) if total > 1e-9 else None


def attention(facts, drift_pp=DRIFT_PP):
    """Lista de cosas que merecen una mirada. Nunca una recomendación.

    `facts` es un diccionario de hechos YA calculados; esta función no pide
    nada a nadie. Devuelve una lista ordenada por gravedad, y cada elemento
    lleva `title` (qué pasa), `scope` (a qué parte afecta), `why` (por qué
    importa) y `missing` (qué dato falta, o None).
    """
    pos = facts.get("positions") or []
    total = float(facts.get("total") or 0.0)
    out = []

    def add(level, key, title, scope, why, missing=None):
        out.append({"level": level, "key": key, "title": title,
                    "scope": scope, "why": why, "missing": missing})

    # ── el dinero que no se puede ni valorar ─────────────────────────────
    sin_valorar = [p for p in pos if not p.get("valued")]
    if sin_valorar:
        add(AVISO, "sin_valorar",
            f"{len(sin_valorar)} posición(es) no se pueden expresar en euros",
            ", ".join(p["ticker"] for p in sin_valorar[:6]),
            "Quedan fuera de TODOS los totales de la pantalla: el patrimonio "
            "que ves es menor que el que tienes.",
            "; ".join(sorted({p.get("why") or "motivo desconocido" for p in sin_valorar})))

    # ── ventas de más: es un error de datos, no de mercado ───────────────
    over = [p for p in pos if p.get("oversold")]
    if over:
        add(AVISO, "oversold",
            f"Hay ventas de más en {len(over)} posición(es)",
            ", ".join(p["ticker"] for p in over[:6]),
            "Se ha vendido más de lo que consta comprado, así que falta alguna "
            "compra en el libro y el precio medio de esa posición es falso.",
            "la compra que falta")

    if facts.get("n_undated"):
        add(AVISO, "sin_fecha",
            f"{facts['n_undated']} movimiento(s) sin fecha",
            "el libro",
            "Sin fecha no hay tipo de cambio del día ni sitio en la serie: "
            "esos movimientos se ordenan los últimos y no entran en la "
            "rentabilidad por periodos.",
            "la fecha")

    # ── lo que se queda fuera del análisis ───────────────────────────────
    sin_hist = [p for p in pos if p.get("valued") and not p.get("has_history")]
    if sin_hist:
        peso = _pct(sum(p.get("market_value") or 0.0 for p in sin_hist), total)
        add(AVISO if (peso or 0) >= 10 else INFO, "sin_historico",
            f"{len(sin_hist)} posición(es) sin histórico utilizable",
            f"{peso:.0f}% del patrimonio" if peso is not None else "—",
            "Se valoran bien hoy, pero no entran en la rentabilidad, ni en la "
            "caída máxima, ni en la correlación: esas cifras describen el "
            "resto de la cartera, no toda.",
            "una serie de precios que la fuente no publica para ese símbolo")

    sin_ter = [p for p in pos if p.get("valued") and p.get("ter") is None]
    if sin_ter:
        peso = _pct(sum(p.get("market_value") or 0.0 for p in sin_ter), total)
        add(INFO, "sin_ter",
            f"{len(sin_ter)} posición(es) sin gastos corrientes declarados",
            f"{peso:.0f}% del patrimonio" if peso is not None else "—",
            "El coste anual que ves está calculado sólo sobre el resto. No es "
            "que estas no cuesten: es que no se sabe cuánto.",
            "el TER, que está en el KID/DFI del producto")

    # ── desviación del plan propio ───────────────────────────────────────
    fuera = [p for p in pos
             if p.get("target") is not None and p.get("weight") is not None
             and abs(p["weight"] - p["target"]) >= drift_pp]
    for p in sorted(fuera, key=lambda x: -abs(x["weight"] - x["target"])):
        d = p["weight"] - p["target"]
        add(INFO, f"drift:{p['ticker']}",
            f"{p['ticker']} pesa {abs(d):.1f} puntos {'más' if d > 0 else 'menos'} "
            f"que tu objetivo",
            f"{p['weight']:.1f}% ahora · {p['target']:.1f}% objetivo",
            "Es una desviación respecto a lo que TÚ decidiste. El panel no "
            "opina sobre el activo ni sabe cuándo necesitas el dinero.",
            None)

    # ── concentración ────────────────────────────────────────────────────
    n_pos = len([p for p in pos if p.get("valued")]) or 1
    umbral = max(CONCENTRACION_PCT, CONCENTRACION_VECES * 100.0 / n_pos)
    gordas = [p for p in pos if (p.get("weight") or 0) >= umbral]
    for p in gordas:
        add(INFO, f"concentracion:{p['ticker']}",
            f"{p['ticker']} es {p['weight']:.0f}% de la cartera",
            f"{p['weight']:.0f}% del patrimonio",
            f"Con {n_pos} posiciones, un reparto igual daría "
            f"{100.0 / n_pos:.0f}% a cada una. Lo que le pase a ésta le pasa a "
            "tu cartera.",
            None)

    eff_c, eff_w = facts.get("eff_n_corr"), facts.get("eff_n_weights")
    if eff_c is not None and eff_c < EFF_N_MIN:
        add(INFO, "diversificacion",
            f"Tus posiciones se comportan como {eff_c:.1f} apuestas independientes",
            f"{len(pos)} posiciones"
            + (f", que contando líneas serían {eff_w:.1f}" if eff_w else ""),
            "Contar líneas no es diversificar: varias posiciones que se mueven "
            "juntas son una sola apuesta repartida en varias filas.",
            None)

    # ── splits sin resolver: el que rompe los números en silencio ────────
    for s in (facts.get("splits") or []):
        add(AVISO, f"split:{s['ticker']}:{s['date']}",
            f"{s['ticker']} hizo un {s['kind']} el {s['date']} "
            f"({s['ratio']:g} por 1) posterior a {s['n_movements']} de tus movimientos",
            f"{s['ticker']}: {s['qty_now']:g} títulos hoy",
            "La serie de precios ya viene ajustada por ese split, así que sólo "
            "cuadra con la cantidad POSTERIOR. Si tus apuntes están en la escala "
            "vieja, el valor de hoy y todo el histórico salen divididos por "
            f"{s['ratio']:g} — sin que nada dé error.",
            "saber si tus cantidades ya lo tienen en cuenta: eso no se puede "
            "deducir del número")

    # ── ritmo de aportación ──────────────────────────────────────────────
    meses = facts.get("months_since_contribution")
    if meses is not None and meses >= MESES_SIN_APORTAR:
        add(INFO, "sin_aportar",
            f"Llevas {meses} meses sin aportar",
            "el libro de movimientos",
            "Es un hecho sobre tus apuntes, no un consejo. Si has aportado y "
            "no está anotado, todo lo que se calcula sobre aportaciones —la "
            "TIR incluida— está midiendo otra cosa.",
            "los movimientos que falten por anotar")

    cob = facts.get("coverage_pct")
    if cob is not None and cob < COBERTURA_BAJA_PCT:
        add(AVISO, "cobertura",
            f"El análisis cubre el {cob:.0f}% de tu patrimonio",
            f"{100 - cob:.0f}% sin cubrir",
            "Rentabilidad, riesgo y correlación describen sólo la parte "
            "cubierta. No es lo mismo que estar bien: es que no se sabe.",
            None)

    orden = {AVISO: 0, INFO: 1}
    out.sort(key=lambda a: orden.get(a["level"], 9))
    return out


# ── calendario de aportaciones ────────────────────────────────────────────
def monthly_flows(dates, flows, divs=None):
    """Agrega los flujos diarios por mes natural.

    `flows` sigue el convenio del paquete: compra positiva, venta negativa. Se
    separan en dos columnas EN VEZ de netearse, porque «aporté 1.000 y retiré
    1.000» y «no hice nada» son dos meses muy distintos y el neto los confunde.

    Devuelve filas `{"month": "YYYY-MM", "in", "out", "div", "net"}` sin huecos:
    los meses en los que no pasó nada aparecen a cero. Comprimir el calendario
    saltándose los meses vacíos dibuja una barra pegada a la siguiente y hace
    parecer constante un ritmo que tuvo parones.
    """
    n = len(dates)
    if n == 0 or len(flows) != n:
        return []
    divs = divs if divs is not None else [0.0] * n
    acc = {}
    for i in range(n):
        k = str(dates[i])[:7]
        a = acc.setdefault(k, {"month": k, "in": 0.0, "out": 0.0, "div": 0.0})
        f = float(flows[i])
        if f > 0:
            a["in"] += f
        elif f < 0:
            a["out"] += -f
        a["div"] += float(divs[i])
    filas = [acc[k] for k in sorted(acc)]
    completo = []
    for k in _month_range(filas[0]["month"], filas[-1]["month"]):
        a = acc.get(k) or {"month": k, "in": 0.0, "out": 0.0, "div": 0.0}
        a["net"] = round(a["in"] - a["out"], 2)
        for c in ("in", "out", "div"):
            a[c] = round(a[c], 2)
        completo.append(a)
    return completo


def _month_range(desde, hasta):
    y0, m0 = int(desde[:4]), int(desde[5:7])
    y1, m1 = int(hasta[:4]), int(hasta[5:7])
    while (y0, m0) <= (y1, m1):
        yield f"{y0:04d}-{m0:02d}"
        m0 += 1
        if m0 > 12:
            y0, m0 = y0 + 1, 1


def contribution_stats(rows, today=None):
    """Resumen del calendario: acumulado, medias, última aportación y parón.

    La media mensual se calcula sobre los meses TRANSCURRIDOS, incluidos los
    que no tuvieron aportación. Dividir sólo entre los meses en que se aportó
    respondería «cuánto aporto cuando aporto», que no es lo que nadie quiere
    saber, y saldría siempre un número más alto y más halagador.
    """
    if not rows:
        return {"total_in": 0.0, "total_out": 0.0, "total_div": 0.0,
                "months": 0, "avg_month": None, "avg_year": None,
                "last_month": None, "months_since": None, "n_months_with_in": 0}
    total_in = sum(r["in"] for r in rows)
    total_out = sum(r["out"] for r in rows)
    total_div = sum(r["div"] for r in rows)
    meses = len(rows)
    con_aporte = [r for r in rows if r["in"] > 1e-9]
    ultimo = con_aporte[-1]["month"] if con_aporte else None
    desde = _months_between(ultimo, (today or rows[-1]["month"])) if ultimo else None
    return {"total_in": round(total_in, 2), "total_out": round(total_out, 2),
            "total_div": round(total_div, 2), "months": meses,
            "avg_month": round(total_in / meses, 2) if meses else None,
            "avg_year": round(total_in / meses * 12, 2) if meses else None,
            "last_month": ultimo, "months_since": desde,
            "n_months_with_in": len(con_aporte)}


def _months_between(a, b):
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(str(b)[:4]), int(str(b)[5:7])
    return max(0, (yb - ya) * 12 + (mb - ma))


# ── progreso contra el plan propio ────────────────────────────────────────
def goal_progress(goal, value, contributed_12m=None):
    """Cuánto se ha avanzado hacia el objetivo, sin prometer cuándo se llega.

    Devuelve progreso y desviación, y NADA de proyección. Decir «a este ritmo
    llegas en 2034» exige suponer una rentabilidad futura, y este panel tiene
    medido que no sabe pronosticar. Un progreso es un hecho comprobable; una
    fecha de llegada es una predicción disfrazada de aritmética.
    """
    if not goal:
        return None
    objetivo = goal.get("capital")
    out = {"capital": objetivo, "value": value,
           "monthly": goal.get("monthly"), "horizon_years": goal.get("horizon_years")}
    out["pct"] = (round(value / objetivo * 100, 1)
                  if (objetivo and objetivo > 1e-9 and value is not None) else None)
    out["missing"] = (round(objetivo - value, 2)
                      if (objetivo and value is not None) else None)

    prevista = goal.get("monthly")
    if prevista and contributed_12m is not None:
        plan = prevista * 12
        out["plan_12m"] = round(plan, 2)
        out["real_12m"] = round(contributed_12m, 2)
        out["plan_pct"] = round(contributed_12m / plan * 100, 1) if plan > 1e-9 else None
    return out
