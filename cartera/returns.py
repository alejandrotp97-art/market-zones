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
    for i, f in factors(values, flows):
        if f is None:
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


def factors(values, flows):
    """Genera `(i, factor)` para cada día, con `factor=None` si no es medible.

    Un solo sitio decide qué tramo cuenta. Antes esta lógica vivía dentro del
    TWR, y la serie de NAV —que tiene que encadenar exactamente los mismos
    tramos— la habría reimplementado: dos copias de la misma regla es como una
    caída máxima acaba sin corresponderse con la rentabilidad que hay al lado.
    """
    n = len(values)
    for i in range(1, n):
        v0, v1, cf = values[i - 1], values[i], flows[i]
        floor = max(MIN_CAPITAL, MIN_CAPITAL_VS_FLOW * abs(cf))
        if v0 <= floor:
            yield i, None
            continue
        f = (v1 - cf) / v0
        # Un factor no positivo significaría -100% o peor en un día. En una
        # cartera real eso es un dato incoherente, no una ruina: se excluye,
        # porque encadenar un cero deja la serie clavada a cero para siempre y
        # borra toda la historia posterior.
        yield i, (f if f > 0 else None)


def nav_series(values, flows):
    """Cuánto vale 1 € invertido el primer día. Empieza en 1,0.

    ESTA es la serie sobre la que se mide una caída, y no la de euros.

    El valor en euros de una cartera sube cuando se aporta, y aportar no es
    recuperarse de nada. Medir la caída máxima sobre los euros hace que una
    transferencia de 10.000 € tape un desplome del 30% y lo convierta en un
    máximo nuevo: la cartera aparecería como si nunca hubiera caído. Al revés
    también: retirar dinero se leería como una pérdida.

    El índice de rendimiento no tiene ese problema porque los flujos ya están
    neutralizados tramo a tramo. Un día no medible deja la serie PLANA, que es
    lo único honesto: no se sabe qué pasó, y no es que no pasara nada.
    """
    nav = [1.0]
    for _i, f in factors(values, flows):
        nav.append(nav[-1] * f if f is not None else nav[-1])
    return nav


def drawdown(nav, dates=None):
    """Caída máxima desde máximos, sobre el índice de rendimiento.

    Devuelve el peor tramo con sus fechas, si se recuperó y cuánto tardó, y la
    caída en la que se está AHORA. Esa última es la que suele importar: una
    caída máxima del 30% en 2020 no dice nada de si hoy estás por debajo de tu
    mejor momento.
    """
    if not nav:
        return None
    pico, pico_i = nav[0], 0
    peor, p_i, v_i = 0.0, 0, 0
    for i, v in enumerate(nav):
        if v > pico:
            pico, pico_i = v, i
        caida = v / pico - 1.0 if pico > 0 else 0.0
        if caida < peor:
            peor, p_i, v_i = caida, pico_i, i

    # ¿Volvió a tocar el pico del que se cayó?
    rec_i = None
    if peor < 0:
        objetivo = nav[p_i]
        for i in range(v_i + 1, len(nav)):
            if nav[i] >= objetivo:
                rec_i = i
                break

    maximo = max(nav)
    actual = nav[-1] / maximo - 1.0 if maximo > 0 else 0.0

    def fecha(i):
        return str(dates[i])[:10] if (dates is not None and i is not None) else None

    def dias(a, b):
        if dates is None or a is None or b is None:
            return None
        try:
            return int((dates[b] - dates[a]).days)
        except Exception:
            return None

    return {"max": peor, "peak": fecha(p_i), "trough": fecha(v_i),
            "recovered": fecha(rec_i),
            "days_down": dias(p_i, v_i),
            "days_to_recover": dias(v_i, rec_i),
            "current": actual,
            "at_high": actual > -1e-9}


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


def volatility(nav, per_year=252):
    """Volatilidad anualizada de la serie de rendimiento, o None.

    Se mide sobre el NAV y no sobre los euros por la misma razón que la caída
    máxima: el día de una aportación, el valor en euros pega un salto que no es
    un movimiento de mercado, y ese salto entraría en la desviación típica como
    si lo fuera. Una cartera a la que se aporta todos los meses saldría mucho
    más volátil de lo que es.

    Los días PLANOS —los tramos que no se pudieron medir— se descartan en vez de
    contarse como un 0% de variación: un cero es un dato, y aquí lo que hay es
    la ausencia de dato. Contarlos hundiría la volatilidad hacia abajo.
    """
    rets = _nav_returns(nav)
    n = len(rets)
    if n < 20:
        return None
    media = sum(rets) / n
    var = sum((r - media) ** 2 for r in rets) / (n - 1)      # muestral, no poblacional
    return math.sqrt(var) * math.sqrt(per_year)


def sharpe(annualized_return, vol, risk_free=0.0):
    """Rentabilidad por unidad de riesgo. None si falta cualquiera de las dos.

    `risk_free` va explícito y por defecto a cero: un Sharpe sin decir contra
    qué tipo sin riesgo se calcula no se puede comparar con ningún otro, y el
    valor por defecto de casi todas las pantallas es cero sin que lo pongan.
    Aquí quien lo pinta tiene que escribirlo al lado.
    """
    if annualized_return is None or vol is None or vol <= 1e-9:
        return None
    return (annualized_return - risk_free) / vol


def _nav_returns(nav):
    """Rendimientos diarios del NAV, saltándose los tramos planos."""
    out = []
    for i in range(1, len(nav)):
        a, b = nav[i - 1], nav[i]
        if a <= 0 or b <= 0 or b == a:
            continue
        out.append(b / a - 1.0)
    return out


def rebalance_with_cash(current, targets, cash):
    """Cómo repartir una aportación para acercarse al objetivo SIN VENDER NADA.

    `current` y `targets` son dicts por instrumento: valor actual en euros y
    peso objetivo en tanto por ciento. Devuelve cuánto comprar de cada uno.

    Rebalancear vendiendo lo que sobra es lo que hace todo el mundo y es la
    versión cara: en España cada venta con plusvalía es un hecho imponible, y
    pagar impuestos hoy para cuadrar unos decimales de peso destruye más valor
    del que corrige. Comprar lo que falta con dinero nuevo llega al mismo sitio
    sin pasar por Hacienda.

    El reparto: se calcula el valor que DEBERÍA tener cada posición sobre el
    total futuro, y el dinero va primero a cubrir los déficits. Si no llega
    para todos, se reparte en proporción al déficit —el que más lejos está
    recibe más—; si sobra, el resto va según los pesos objetivo.
    """
    cash = float(cash or 0.0)
    if cash <= 0 or not targets:
        return {}
    total_obj = sum(float(v) for v in targets.values() if v)
    if total_obj <= 0:
        return {}
    # Los objetivos se normalizan: si suman 90 o 110, la intención sigue siendo
    # la proporción entre ellos, y fallar por no sumar 100 exacto sería exigir
    # una aritmética que nadie hace a mano.
    w = {k: float(v) / total_obj for k, v in targets.items() if v}
    futuro = sum(float(v) for v in current.values()) + cash
    deficit = {k: max(0.0, w[k] * futuro - float(current.get(k, 0.0))) for k in w}
    suma_def = sum(deficit.values())

    if suma_def <= 1e-9:
        return {k: round(cash * w[k], 2) for k in w}
    if suma_def >= cash:
        # No llega para cuadrar del todo: proporcional al déficit.
        return {k: round(cash * d / suma_def, 2) for k, d in deficit.items() if d > 1e-9}
    sobra = cash - suma_def
    return {k: round(deficit[k] + sobra * w[k], 2) for k in w
            if deficit[k] + sobra * w[k] > 1e-9}
