"""TWR, TIR y la descomposición de divisa, comprobadas contra resultados
conocidos y contra casos calculados a mano.

Estas tres fórmulas tienen una propiedad incómoda: **cuando están mal, siguen
devolviendo un número creíble**. Un TWR que no neutraliza las aportaciones, o
una TIR que converge a una raíz absurda, salen en pantalla con sus dos
decimales y nadie los discute. Así que casi todo lo de aquí es una IDENTIDAD
que la fórmula tiene que cumplir sí o sí, no un valor de referencia suelto.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from cartera.returns import (DAYS_YEAR, annualize, beta, currency_split,
                             drawdown, effective_n, nav_series,
                             rebalance_with_cash, sharpe, twr, volatility, xirr)


# ── TIR contra el valor publicado ─────────────────────────────────────────
def test_xirr_reproduce_el_ejemplo_de_la_documentacion_de_excel():
    """El caso de la ficha de la función XIRR de Microsoft. Es la referencia
    externa: si esto se mueve, la implementación ha cambiado de convenio."""
    flujos = [(date(2008, 1, 1), -10000), (date(2008, 3, 1), 2750),
              (date(2008, 10, 30), 4250), (date(2009, 2, 15), 3250),
              (date(2009, 4, 1), 2750)]
    assert xirr(flujos) == pytest.approx(0.373362535, abs=1e-7)


def test_xirr_usa_base_actual_365_como_excel():
    """Duplicar el dinero en 366 días (2020 fue bisiesto) NO es un 100%: es
    2^(365/366)-1. Un año de 360 o de 366 días daría otro número, y elegir mal
    la base descuadra toda comparación con una hoja de cálculo."""
    r = xirr([(date(2020, 1, 1), -100), (date(2021, 1, 1), 200)])
    assert r == pytest.approx(2 ** (365 / 366) - 1, abs=1e-9)


def test_xirr_de_un_ano_exacto_es_la_rentabilidad_simple():
    r = xirr([(date(2021, 1, 1), -1000), (date(2022, 1, 1), 1150)])
    assert r == pytest.approx(0.15, abs=1e-9)


def test_xirr_devuelve_none_cuando_no_hay_respuesta():
    """Sin cambio de signo no existe TIR. Devolver un cero, o el resultado al
    que se haya arrastrado el solucionador, sería inventarse una cifra."""
    assert xirr([(date(2020, 1, 1), -100), (date(2021, 1, 1), -100)]) is None
    assert xirr([(date(2020, 1, 1), 100), (date(2021, 1, 1), 100)]) is None
    assert xirr([(date(2020, 1, 1), -100)]) is None
    assert xirr([]) is None


def test_xirr_aguanta_una_rentabilidad_enorme():
    """Una cartera joven que ha multiplicado por diez en tres meses tiene una
    TIR anualizada de CINCO CIFRAS. Es un número absurdo de enseñar, pero es el
    número: dejarlo fuera del intervalo lo convertiría en un «no hay solución»,
    que es mentira. Quien lo pinta decide si tiene sentido anualizar tan poca
    historia — el solucionador sólo tiene que encontrar la raíz.
    """
    r = xirr([(date(2024, 1, 1), -100), (date(2024, 4, 1), 1000)])
    assert r is not None
    # 10x en 91 días: (10)^(365/91) - 1
    assert r == pytest.approx(10 ** (365 / 91) - 1, rel=1e-6)


def test_xirr_de_una_perdida_casi_total_sigue_teniendo_solucion():
    r = xirr([(date(2020, 1, 1), -1000), (date(2021, 1, 1), 1)])
    assert r is not None and r < -0.99


def test_xirr_no_se_despista_con_muchos_flujos_pequenos():
    """Aportación mensual pequeña durante cinco años: es la forma de la cartera
    real de casi todo el mundo, y donde Newton-Raphson se va a una raíz absurda
    porque la función tiene muy poca pendiente. La bisección no."""
    flujos = [(date(2019 + m // 12, m % 12 + 1, 1), -100.0) for m in range(60)]
    flujos.append((date(2024, 1, 1), 7500.0))
    r = xirr(flujos)
    assert r is not None
    assert 0.05 < r < 0.30                       # un rango amplio, pero finito


# ── TWR: las identidades que tiene que cumplir ────────────────────────────
def test_twr_no_se_mueve_por_cuando_se_aporto():
    """LA propiedad. Es lo que separa el TWR de un porcentaje cualquiera: si
    aportar más a mitad de camino cambiara el resultado, no sería comparable
    con un índice, que es lo único para lo que sirve."""
    t = twr([100, 110, 210, 231], [100, 0, 100, 0])
    assert t["total"] == pytest.approx(0.21, abs=1e-12)


def test_dos_carteras_con_los_mismos_activos_y_distinto_calendario_empatan():
    """Misma trayectoria de precios, aportaciones distintas: mismo TWR.

    OJO al montar el caso: si se aportan 1000 el último día, el valor de cierre
    de ESE día es 121 (lo que ya había) + 1000 (lo recién comprado, valorado a
    su propio cierre) = 1121. Poner 1210 sería afirmar que el dinero nuevo
    también subió un 10% el mismo día que entró, o sea otra trayectoria de
    precios — y entonces el test no compara lo que dice comparar.
    """
    a = twr([100, 110, 121], [100, 0, 0])
    b = twr([100, 110, 1121], [100, 0, 1000])
    assert a["total"] == pytest.approx(b["total"], abs=1e-12)
    assert a["total"] == pytest.approx(0.21, abs=1e-12)


def test_un_dividendo_no_se_lee_como_perdida():
    """El precio cae por el importe repartido y el valor de los títulos baja
    sin que se haya perdido nada. El dividendo entra como retirada y compensa
    exactamente esa caída. Sin eso, cada reparto restaba rentabilidad."""
    assert twr([100, 95], [100, -5])["total"] == pytest.approx(0.0, abs=1e-12)


def test_una_venta_no_es_una_perdida():
    t = twr([1000, 500], [0, -500])              # vende la mitad, sin variación
    assert t["total"] == pytest.approx(0.0, abs=1e-12)


def test_el_desglose_por_ano_multiplica_hasta_el_total():
    """Cada año es un tramo de la misma cadena: el producto de los años tiene
    que reconstruir el total. Si no, uno de los dos está calculado aparte."""
    import pandas as pd
    fechas = pd.to_datetime(["2023-12-30", "2023-12-31", "2024-06-30", "2024-12-31"])
    t = twr([100, 110, 121, 133.1], [100, 0, 0, 0], dates=list(fechas))
    prod = 1.0
    for y in t["by_year"]:
        prod *= 1 + y["ret"]
    assert prod == pytest.approx(1 + t["total"], rel=1e-12)
    assert [y["year"] for y in t["by_year"]] == [2023, 2024]


def test_un_tramo_sin_capital_se_salta_y_se_cuenta():
    """Un capital inicial casi nulo divide la diferencia entre el precio de
    compra y el cierre de ese día por casi cero, y escupe un porcentaje
    absurdo. Se excluye, pero se CUENTA: la pantalla tiene que poder decir
    cuántos días no entran en la cifra en vez de disimularlo."""
    t = twr([0.0, 0.0, 1000.0, 1100.0], [0, 0, 1000, 0])
    assert t["skipped"] == 2
    assert t["total"] == pytest.approx(0.10, abs=1e-12)


def test_sin_ningun_tramo_medible_el_total_es_none():
    t = twr([0.0, 0.0], [0, 0])
    assert t["total"] is None and t["periods"] == 0


def test_series_incoherentes_no_devuelven_un_numero():
    assert twr([100], [0])["total"] is None
    assert twr([], [])["total"] is None
    assert twr([100, 110], [0])["total"] is None      # longitudes distintas


# ── anualizar, y cuándo NO ────────────────────────────────────────────────
def test_por_debajo_de_un_ano_no_se_anualiza():
    """Convertir un +8% de tres meses en un +36% anual es la mentira más común
    que hay en una pantalla de inversión: proyecta una racha como si fuera una
    tasa. Se devuelve None y la pantalla dice que no hay bastante historia."""
    assert annualize(0.08, 90) is None
    assert annualize(0.08, 364) is None
    assert annualize(0.08, 365) is not None


def test_anualizar_dos_anos_saca_la_raiz():
    assert annualize(0.21, 730) == pytest.approx(1.21 ** (365 / 730) - 1, rel=1e-12)


def test_anualizar_una_ruina_total_no_revienta():
    assert annualize(-1.0, 800) == -1.0


# ── activo frente a divisa ────────────────────────────────────────────────
def test_la_descomposicion_suma_exactamente_el_no_realizado():
    """No es una aproximación: los dos sumandos reconstruyen el resultado al
    céntimo. Si no cuadrase, uno de los dos estaría midiendo otra cosa y la
    pantalla estaría repartiendo una diferencia inventada."""
    d = currency_split(qty=10, avg_cost_native=100.0, last_native=120.0,
                       fx_now=0.80, cost_native=1000.0, cost_eur=900.0)
    no_realizado = 10 * 120.0 * 0.80 - 900.0
    assert d["asset"] + d["currency"] == pytest.approx(no_realizado, abs=1e-9)


def test_en_euros_la_divisa_no_aporta_nada():
    d = currency_split(qty=10, avg_cost_native=100.0, last_native=120.0,
                       fx_now=1.0, cost_native=1000.0, cost_eur=1000.0)
    assert d["currency"] == pytest.approx(0.0, abs=1e-12)
    assert d["asset"] == pytest.approx(200.0, abs=1e-9)


def test_un_activo_plano_con_la_divisa_en_contra_pierde_solo_por_divisa():
    d = currency_split(qty=10, avg_cost_native=100.0, last_native=100.0,
                       fx_now=0.72, cost_native=1000.0, cost_eur=900.0)
    assert d["asset"] == pytest.approx(0.0, abs=1e-9)
    assert d["currency"] < 0
    assert d["fx_change_pct"] == pytest.approx(-20.0, abs=1e-9)


def test_sin_datos_suficientes_no_se_reparte_nada():
    assert currency_split(0, 100.0, 120.0, 1.0, 0.0, 0.0) is None
    assert currency_split(10, 100.0, None, 1.0, 1000.0, 1000.0) is None
    assert currency_split(10, 100.0, 120.0, None, 1000.0, 1000.0) is None


# ── años parciales ────────────────────────────────────────────────────────
def test_el_primer_y_el_ultimo_ano_se_marcan_como_parciales():
    """Un «2024: +19%» que en realidad cubre de mayo a diciembre se lee como un
    año entero y se compara contra el año entero de un índice. Esa comparación
    no existe, y la fila tiene que llevar sus fechas para impedirla."""
    import pandas as pd
    fechas = list(pd.to_datetime(["2024-05-03", "2024-12-31", "2025-06-30"]))
    t = twr([100, 119, 130], [100, 0, 0], dates=fechas)
    porano = {r["year"]: r for r in t["by_year"]}
    assert porano[2024]["partial"] is True          # empieza en mayo
    assert porano[2024]["from"] == "2024-12-31"     # primer tramo MEDIDO del año
    assert porano[2025]["partial"] is True          # acaba en junio


def test_un_ano_completo_no_se_marca_parcial():
    import pandas as pd
    fechas = list(pd.to_datetime(["2023-12-29", "2024-01-02", "2024-12-30", "2025-01-02"]))
    t = twr([100, 101, 110, 111], [100, 0, 0, 0], dates=fechas)
    porano = {r["year"]: r for r in t["by_year"]}
    assert porano[2024]["partial"] is False


# ── diversificación de verdad ─────────────────────────────────────────────
def test_cinco_posiciones_sin_correlacion_son_cinco_apuestas():
    import numpy as np
    w = [0.2] * 5
    assert effective_n(w, np.eye(5).tolist()) == pytest.approx(5.0, abs=1e-9)


def test_cinco_posiciones_perfectamente_correlacionadas_son_UNA_apuesta():
    """Es el caso que justifica toda la sección: cinco ETFs del mismo índice
    dan «5 activos efectivos» contando líneas, y son una sola apuesta. El
    número que tranquiliza es justo el que está mal."""
    w = [0.2] * 5
    unos = [[1.0] * 5 for _ in range(5)]
    assert effective_n(w, unos) == pytest.approx(1.0, abs=1e-9)


def test_con_la_identidad_coincide_con_la_formula_de_pesos():
    """La versión con correlación GENERALIZA a la de pesos, no la contradice.
    Si divergieran con la identidad, una de las dos estaría mal."""
    import numpy as np
    w = [0.5, 0.3, 0.15, 0.05]
    assert effective_n(w, np.eye(4).tolist()) == pytest.approx(effective_n(w), abs=1e-9)


def test_la_correlacion_solo_puede_bajar_la_diversificacion():
    """Con correlaciones positivas el N efectivo nunca puede subir por encima
    del que dan los pesos. Si subiera, el signo estaría invertido."""
    w = [0.4, 0.35, 0.25]
    r = [[1.0, 0.7, 0.5], [0.7, 1.0, 0.6], [0.5, 0.6, 1.0]]
    assert effective_n(w, r) < effective_n(w)


def test_los_pesos_se_normalizan_solos():
    """Pasarlos en euros o en porcentaje tiene que dar lo mismo: un olvido de
    normalizar saldría como diversificación inflada, no como un error."""
    assert effective_n([2000, 3000, 5000]) == pytest.approx(effective_n([0.2, 0.3, 0.5]), abs=1e-12)


def test_una_matriz_que_no_encaja_no_devuelve_un_numero():
    assert effective_n([0.5, 0.5], [[1.0]]) is None
    assert effective_n([]) is None
    assert effective_n([0.0, 0.0]) is None


# ── caída máxima: por qué NO se mide sobre los euros ──────────────────────
def test_una_aportacion_no_puede_hacer_pasar_por_recuperada_una_caida():
    """EL caso que justifica medir sobre el índice de rendimiento.

    Cae un 30% y al día siguiente entran 300 € que devuelven el valor en euros
    a su máximo anterior. Sobre euros, la serie dice «recuperada». Pero el euro
    invertido sigue valiendo 0,70: los 300 no recuperaron nada, taparon el
    agujero con dinero nuevo. Con aportaciones mensuales, una bajada larga
    puede no llegar a verse NUNCA sobre la serie de euros.
    """
    import pandas as pd
    fechas = list(pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]))
    vals = [1000, 700, 1000, 1000]
    flows = [1000, 0, 300, 0]

    d = drawdown(nav_series(vals, flows), fechas)

    assert d["max"] == pytest.approx(-0.30, abs=1e-9)
    assert d["recovered"] is None                 # sobre euros diría 2024-03-01
    assert d["current"] == pytest.approx(-0.30, abs=1e-9)
    assert d["at_high"] is False


def test_la_caida_se_marca_recuperada_cuando_el_MERCADO_la_recupera():
    import pandas as pd
    fechas = list(pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]))
    d = drawdown(nav_series([1000, 700, 1000], [1000, 0, 0]), fechas)
    assert d["recovered"] == "2024-03-01"
    assert d["at_high"] is True
    assert d["current"] == pytest.approx(0.0, abs=1e-9)


def test_una_cartera_que_solo_sube_no_tiene_caida():
    d = drawdown(nav_series([100, 110, 120], [100, 0, 0]))
    assert d["max"] == 0.0 and d["at_high"] is True


def test_el_nav_empieza_en_uno_y_encadena_los_mismos_tramos_que_el_twr():
    """Si el NAV y el TWR no encadenasen exactamente los mismos factores, la
    caída máxima no se correspondería con la rentabilidad que hay al lado."""
    vals, flows = [100, 110, 210, 231], [100, 0, 100, 0]
    nav = nav_series(vals, flows)
    assert nav[0] == 1.0
    assert nav[-1] - 1.0 == pytest.approx(twr(vals, flows)["total"], abs=1e-12)


def test_un_tramo_no_medible_deja_el_nav_PLANO():
    """No se sabe qué pasó, y eso no es lo mismo que decir que no pasó nada:
    inventar un 0% ahí bajaría la volatilidad y taparía una caída."""
    nav = nav_series([0.0, 0.0, 1000.0, 1100.0], [0, 0, 1000, 0])
    assert nav[:3] == [1.0, 1.0, 1.0]
    assert nav[-1] == pytest.approx(1.10, abs=1e-12)


# ── volatilidad y Sharpe ──────────────────────────────────────────────────
def test_la_volatilidad_no_cuenta_los_dias_planos():
    """Un día plano es ausencia de dato, no un 0% de variación. Contarlo
    hundiría la desviación típica hacia abajo."""
    import math
    base = [1.0]
    for _ in range(60):
        base.append(base[-1] * 1.01)
    v_limpia = volatility(base)
    con_planos = base + [base[-1]] * 60          # 60 días sin dato
    assert volatility(con_planos) == pytest.approx(v_limpia, rel=1e-9)


def test_sin_muestra_suficiente_no_se_publica_una_volatilidad():
    assert volatility([1.0, 1.01, 1.02]) is None


def test_el_sharpe_necesita_las_dos_piezas_y_el_tipo_sin_riesgo_es_explicito():
    assert sharpe(0.10, 0.20) == pytest.approx(0.5, abs=1e-12)
    assert sharpe(0.10, 0.20, risk_free=0.02) == pytest.approx(0.4, abs=1e-12)
    assert sharpe(0.10, None) is None
    assert sharpe(None, 0.2) is None
    assert sharpe(0.10, 0.0) is None             # dividir por cero no es infinito útil


# ── rebalanceo comprando, sin vender ──────────────────────────────────────
def test_el_dinero_nuevo_va_a_lo_que_esta_por_debajo_del_objetivo():
    """Rebalancear vendiendo lo que sobra es la versión cara: en España cada
    venta con plusvalía es un hecho imponible. Comprar lo que falta llega al
    mismo sitio sin pasar por Hacienda."""
    r = rebalance_with_cash({"A": 8000, "B": 2000}, {"A": 50, "B": 50}, 1000)
    assert r == {"B": 1000.0}                    # A ya está por encima: no recibe nada


def test_si_el_dinero_llega_para_cuadrar_el_resto_va_segun_el_objetivo():
    r = rebalance_with_cash({"A": 5000, "B": 3000}, {"A": 50, "B": 50}, 4000)
    assert sum(r.values()) == pytest.approx(4000, abs=0.02)
    # deja las dos en 6000: 5000+1000 y 3000+3000
    assert r["B"] > r["A"]


def test_los_objetivos_se_normalizan_aunque_no_sumen_cien():
    """Exigir que sumen 100 exacto sería pedir una aritmética que nadie hace a
    mano; lo que expresa la intención es la proporción entre ellos."""
    a = rebalance_with_cash({"A": 100, "B": 100}, {"A": 30, "B": 60}, 900)
    b = rebalance_with_cash({"A": 100, "B": 100}, {"A": 33.333, "B": 66.667}, 900)
    assert a["A"] == pytest.approx(b["A"], abs=1.0)


def test_sin_dinero_o_sin_objetivos_no_se_propone_nada():
    assert rebalance_with_cash({"A": 100}, {"A": 100}, 0) == {}
    assert rebalance_with_cash({"A": 100}, {}, 500) == {}


# ── beta contra el índice ─────────────────────────────────────────────────
def test_una_cartera_que_replica_al_indice_tiene_beta_uno():
    import math
    nav = [1.0]
    for i in range(120):
        nav.append(nav[-1] * (1 + 0.01 * math.sin(i)))
    b, r, n = beta(nav, nav)
    assert b == pytest.approx(1.0, abs=1e-9)
    assert r == pytest.approx(1.0, abs=1e-9)
    assert n == 120


def test_una_cartera_que_amplifica_al_indice_tiene_beta_dos():
    import math
    ib, ip = [1.0], [1.0]
    for i in range(120):
        r = 0.005 * math.sin(i)
        ib.append(ib[-1] * (1 + r))
        ip.append(ip[-1] * (1 + 2 * r))
    b, c, _n = beta(ip, ib)
    assert b == pytest.approx(2.0, rel=0.02)
    assert c == pytest.approx(1.0, abs=1e-6)


def test_la_beta_no_se_calcula_sobre_los_euros(monkeypatch):
    """Si se midiera sobre el saldo, el salto del día de una aportación entraría
    como un movimiento de mercado y la beta saldría inflada por transferencias.
    `beta` recibe NAV, así que la prueba es que dos carteras con la misma
    trayectoria y distinto calendario de aportaciones dan la misma beta."""
    import math
    idx = [1.0]
    for i in range(120):
        idx.append(idx[-1] * (1 + 0.004 * math.sin(i)))
    vals_a = [1000 * x for x in idx]
    flows_a = [1000.0] + [0.0] * 120
    vals_b, flows_b = [1000 * idx[0]], [1000.0]
    for i in range(1, 121):
        extra = 500.0 if i == 60 else 0.0
        vals_b.append((vals_b[-1] / idx[i - 1]) * idx[i] + extra)
        flows_b.append(extra)
    ba = beta(nav_series(vals_a, flows_a), idx)[0]
    bb = beta(nav_series(vals_b, flows_b), idx)[0]
    assert ba == pytest.approx(bb, rel=1e-6)


def test_sin_muestra_suficiente_no_se_publica_beta():
    assert beta([1.0, 1.01, 1.02], [1.0, 1.01, 1.02]) == (None, None, 2)


def test_contra_un_indice_plano_la_beta_es_none_y_no_infinito():
    """Dividir entre una varianza de cero no da un número grande: no da nada."""
    b, c, _ = beta([1.0 + 0.001 * i for i in range(60)], [1.0] * 60)
    assert b is None and c is None


def test_un_dia_plano_en_UNA_serie_no_desalinea_las_dos():
    """`_nav_returns` descarta los días sin variación, y una serie puede tener
    uno donde la otra no. Emparejar dos listas de distinta longitud alineándolas
    por el final juntaría el lunes de una con el martes de la otra: la beta
    saldría de comparar días que no se corresponden, y sin dar ningún error."""
    import math
    ib, ip = [1.0], [1.0]
    for i in range(1, 121):
        r = 0.004 * math.sin(i)
        ib.append(ib[-1] * (1 + r))
        # la cartera se queda EXACTAMENTE plana un día suelto
        ip.append(ip[-1] if i == 40 else ip[-1] * (1 + r))
    b, c, n = beta(ip, ib)
    assert n == 120                       # ni un par se pierde por el hueco
    assert c == pytest.approx(1.0, abs=0.05)
    assert b == pytest.approx(1.0, abs=0.05)
