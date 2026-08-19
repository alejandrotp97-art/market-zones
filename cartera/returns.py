"""Rentabilidad de una cartera con aportaciones repartidas en el tiempo.

Puro: entra una serie de valores y una de flujos, sale un número. Ni red, ni
disco, ni reloj. Está aquí y no en el panel porque estas fórmulas son la clase
de cosa que se puede comprobar contra un resultado publicado, y hacerlo no debe
requerir ni Flask ni Yahoo.

POR QUÉ DOS RENTABILIDADES Y NO UNA
-----------------------------------
Un «+89%» sobre una cartera a la que se ha ido metiendo dinero no significa
nada, porque no dice CUÁNDO entró cada euro. Hay dos preguntas distintas y cada
una tiene su número:

  TWR  ¿Qué tal lo han hecho los activos que elegí?
       Encadena el rendimiento de cada tramo entre movimientos, así que el
       momento en que se aportó no la mueve. Es la ÚNICA comparable con un
       índice: un índice tampoco recibe aportaciones.

  TIR  ¿Qué me he llevado yo?
       Descuenta cada flujo por el tiempo que ha estado trabajando. Aportar
       mucho justo antes de una subida la sube; hacerlo antes de una caída la
       baja. Es la que responde «¿cuánto rinde MI dinero?».

Y la diferencia entre las dos no es ruido: **es el efecto de tu propio
timing**. Si la TIR va por encima de la TWR, aportaste bien. Si va por debajo,
los mismos activos te habrían rendido más entrando de otra forma.

EL CONVENIO DE LOS FLUJOS, QUE ES DONDE SE FALLA
------------------------------------------------
`values[i]` es el valor de los TÍTULOS al cierre del día i, con el movimiento
de ese día ya dentro (la reconstrucción suma las participaciones compradas ese
día valoradas a su cierre). Por tanto el flujo del día i ya está incorporado a
`values[i]`, y el rendimiento del tramo es

    f_i = (values[i] - flows[i]) / values[i-1]

Un dividendo entra como flujo NEGATIVO, es decir, como una retirada. Parece
contraintuitivo y es justo lo que hace que la cifra salga bien: el día que un
activo reparte, su precio cae por el importe repartido, así que el valor de los
títulos baja sin que se haya perdido nada. Restar el dividendo como salida
compensa exactamente esa caída.

    Comprar 1 título a 100. Reparte 5 y el precio queda en 95.
    f = (95 - (-5)) / 100 = 1,00  ->  0%. Que es la verdad: 95 en título más
    5 en el bolsillo.

Sin ese tratamiento, el reparto se leería como una pérdida del 5%.
"""
from __future__ import annotations

import math

# Un tramo cuyo capital inicial es despreciable frente al flujo del día no mide
# nada: divide una diferencia de céntimos (la que hay entre el precio al que se
# compró y el cierre de ese día) por un capital casi nulo, y devuelve un
# porcentaje absurdo. Esos tramos se saltan Y SE CUENTAN, para que la pantalla
# pueda decir cuántos días no entran en la cifra en vez de disimularlo.
MIN_CAPITAL = 1.0          # euros
MIN_CAPITAL_VS_FLOW = 0.01  # y al menos el 1% del flujo del día

DAYS_YEAR = 365.0


def twr(values, flows, dates=None):
    """Time-Weighted Return encadenando los tramos diarios.

    `values[i]`  valor de los títulos al cierre del día i (flujo ya dentro)
    `flows[i]`   flujo externo neto del día i: compra +, venta -, dividendo -
    `dates[i]`   fecha del día i, sólo para el desglose por año

    Devuelve un dict con el total, el anualizado, el desglose por año y los
    tramos excluidos. `total` es None cuando no hay ni un tramo medible.
    """
    n = len(values)
    if n < 2 or len(flows) != n:
        return {"total": None, "annualized": None, "by_year": [], "skipped": 0,
                "periods": 0, "days": 0}

    chain, skipped, per_year = 1.0, 0, {}
    for i in range(1, n):
        v0, v1, cf = values[i - 1], values[i], flows[i]
        floor = max(MIN_CAPITAL, MIN_CAPITAL_VS_FLOW * abs(cf))
        if v0 <= floor:
            skipped += 1
            continue
        f = (v1 - cf) / v0
        if f <= 0:
            # Un factor no positivo significaría -100% o peor en un día. En una
            # cartera real eso es un dato incoherente, no una ruina: se excluye
            # y se cuenta, porque encadenar un cero deja la serie clavada a cero
            # para siempre y borra toda la historia posterior.
            skipped += 1
            continue
        chain *= f
        if dates is not None:
            y = _year(dates[i])
            acc = per_year.setdefault(y, {"f": 1.0, "first": dates[i], "last": dates[i]})
            acc["f"] *= f
            acc["last"] = dates[i]

    periods = (n - 1) - skipped
    if periods <= 0:
        return {"total": None, "annualized": None, "by_year": [], "skipped": skipped,
                "periods": 0, "days": 0}

    days = _span_days(dates, n)
    total = chain - 1.0
    return {"total": total,
            "annualized": annualize(total, days),
            "by_year": _year_rows(per_year),
            "skipped": skipped, "periods": periods, "days": days}


def _year(d):
    return d.year if hasattr(d, "year") else int(str(d)[:4])


def _year_rows(per_year):
    """Cada año con su tramo REAL de calendario.

    El primer y el último año de una cartera casi nunca son años completos. Un
    «2024: +19%» que en realidad cubre de mayo a diciembre se lee como un año
    entero y se compara con el año entero de un índice, que es una comparación
    que no existe. Cada fila lleva sus fechas y su marca de parcial para que la
    pantalla no pueda callárselo.
    """
    rows = []
    for y, acc in sorted(per_year.items()):
        d0, d1 = acc["first"], acc["last"]
        parcial = not (_mmdd(d0) <= (1, 15) and _mmdd(d1) >= (12, 15))
        rows.append({"year": y, "ret": acc["f"] - 1.0,
                     "from": str(d0)[:10], "to": str(d1)[:10],
                     "partial": parcial})
    return rows


def _mmdd(d):
    try:
        return (d.month, d.day)
    except AttributeError:
        s = str(d)[:10]
        return (int(s[5:7]), int(s[8:10]))


def annualize(total, days):
    """Rentabilidad anualizada, o None si el periodo es demasiado corto.

    Por debajo de un año NO se anualiza. Convertir un +8% de tres meses en un
    +36% anual es la mentira estadística más común que hay en una pantalla de
    inversión: proyecta una racha como si fuera una tasa.
    """
    if total is None or days is None or days < DAYS_YEAR:
        return None
    if total <= -1.0:
        return -1.0
    return (1.0 + total) ** (DAYS_YEAR / days) - 1.0


def _span_days(dates, n):
    if dates is None or n < 2:
        return 0
    try:
        return max(0, int((dates[n - 1] - dates[0]).days))
    except Exception:
        return 0


def xirr(flows, guess_lo=-0.999999, guess_hi=10.0, tol=1e-10, max_iter=300):
    """Tasa interna de retorno de flujos en fechas arbitrarias (la XIRR de Excel).

    `flows` es una lista de (fecha, importe) con el convenio de caja de quien
    invierte: **negativo lo que sale de tu bolsillo** (una compra), positivo lo
    que entra (una venta, un dividendo, y el valor de mercado de hoy como flujo
    final). Base actual/365, la misma que usa Excel.

    Se resuelve por BISECCIÓN y no por Newton-Raphson a propósito. Newton es
    más rápido y aquí no hace falta velocidad ninguna —son unos cientos de
    flujos—, pero puede divergir o caer en una raíz absurda cuando la función
    tiene poca pendiente, que es exactamente lo que pasa con una cartera de
    aportaciones pequeñas y frecuentes. La bisección no diverge nunca: si hay
    cambio de signo en el intervalo, converge.

    Devuelve None cuando no hay solución en el intervalo, que es la respuesta
    honesta —y el caso típico es real: todos los flujos del mismo signo, o sea
    una cartera a la que sólo se ha aportado y de la que nunca ha salido nada
    ni queda valor. Un número inventado ahí sería peor que un hueco.
    """
    flows = [(d, float(a)) for d, a in flows if a is not None and abs(float(a)) > 1e-12]
    if len(flows) < 2:
        return None
    if not (any(a < 0 for _d, a in flows) and any(a > 0 for _d, a in flows)):
        return None                       # sin cambio de signo no hay TIR

    flows.sort(key=lambda x: x[0])
    d0 = flows[0][0]
    ts = [((d - d0).days / DAYS_YEAR, a) for d, a in flows]

    def npv(r):
        acc = 0.0
        for t, a in ts:
            try:
                acc += a / (1.0 + r) ** t
            except (OverflowError, ZeroDivisionError):
                return math.inf if a > 0 else -math.inf
        return acc

    lo = guess_lo
    f_lo = npv(lo)
    if not math.isfinite(f_lo):
        return None
    # El techo se ESCALA hasta que haya cambio de signo. Una cartera de tres
    # meses que ha multiplicado por diez tiene una TIR anualizada de cinco
    # cifras: es un número absurdo de enseñar, pero es el número, y dejarlo
    # fuera del intervalo lo convertiría en un «no hay solución» que es
    # mentira. Quien lo pinta decide si tiene sentido anualizar tan poca
    # historia; este solucionador sólo tiene que encontrar la raíz.
    hi = f_hi = None
    for techo in (guess_hi, 100.0, 1e4, 1e6):
        v = npv(techo)
        if math.isfinite(v) and f_lo * v <= 0:
            hi, f_hi = techo, v
            break
    if hi is None:
        return None

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if not math.isfinite(f_mid):
            return None
        if abs(f_mid) < 1e-9 or (hi - lo) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def currency_split(qty, avg_cost_native, last_native, fx_now, cost_native, cost_eur):
    """Parte del resultado que puso el ACTIVO y parte que puso la DIVISA.

    La descomposición es EXACTA, no una aproximación, y por eso se puede
    enseñar sumada:

        efecto activo = qty x (last - coste_medio) x fx_compra
        efecto divisa = qty x last x (fx_hoy - fx_compra)
        --------------------------------------------------
        suma          = qty x last x fx_hoy - qty x coste_medio x fx_compra
                      = valor de mercado - coste          = resultado no realizado

    `fx_compra` es el cambio medio ponderado de las compras, y sale de dividir
    el coste en euros entre el coste en divisa nativa: los dos ya están
    calculados con el cambio del día de cada compra, así que su cociente ES el
    cambio medio al que se entró. No hay que volver a pedir ninguna serie.
    """
    if not qty or last_native is None or fx_now is None:
        return None
    if not cost_native or abs(cost_native) < 1e-12 or cost_eur is None:
        return None
    fx_buy = cost_eur / cost_native
    if avg_cost_native is None:
        return None
    asset = qty * (last_native - avg_cost_native) * fx_buy
    currency = qty * last_native * (fx_now - fx_buy)
    return {"asset": asset, "currency": currency,
            "fx_buy": fx_buy, "fx_now": fx_now,
            "fx_change_pct": (fx_now / fx_buy - 1.0) * 100.0 if fx_buy else None}


def effective_n(weights, corr=None):
    """Número EFECTIVO de apuestas de una cartera.

        por pesos          1 / SUM(w_i^2)
        por correlación    1 / SUM_ij(w_i w_j rho_ij)

    La segunda generaliza a la primera y no la contradice: con la matriz
    identidad devuelve exactamente la primera. Los casos extremos son los que
    dicen si está bien planteada:

        cinco posiciones iguales sin correlación  ->  5,0   (cinco apuestas)
        cinco posiciones iguales correlacionadas  ->  1,0   (UNA apuesta en
                                                            cinco líneas)

    Contar líneas no es diversificar. Un `1/SUM(w^2)` a secas dice «5» en los
    dos casos, y ése es justo el número que tranquiliza a quien tiene cinco
    ETFs del mismo índice.

    Los pesos se normalizan aquí: quien llama no tiene por qué acordarse, y un
    olvido saldría como una diversificación inflada en vez de como un error.
    """
    w = [float(x) for x in weights if x is not None]
    if not w:
        return None
    total = sum(w)
    if total <= 0:
        return None
    w = [x / total for x in w]
    n = len(w)
    if corr is None:
        q = sum(x * x for x in w)
    else:
        if len(corr) != n or any(len(row) != n for row in corr):
            return None
        q = 0.0
        for i in range(n):
            for j in range(n):
                q += w[i] * w[j] * float(corr[i][j])
    return (1.0 / q) if q > 1e-12 else None
