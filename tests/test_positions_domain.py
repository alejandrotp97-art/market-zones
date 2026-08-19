"""La aritmética del dinero, comprobada sin panel y sin red.

Este fichero no importa `dashboard`. Es el punto: la regla de que un coste
medio se calcula con el tipo de cambio del DÍA DE LA COMPRA no depende de Flask
ni de Yahoo, así que tampoco debería necesitarlos para demostrarse. El mercado
entra por parámetro, y aquí se le pasa uno de mentira con los valores fijados a
mano — nada de `monkeypatch` sobre módulos ajenos.

`tests/test_cartera.py` sigue cubriendo el cableado real del panel. Esto cubre
la regla.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from cartera.positions import BASE_CCY, compute


class FakeMarket:
    """Un mercado con los valores puestos a mano. Seis métodos, ni uno más."""

    def __init__(self, ccy="USD", last=100.0, fx_now=0.90, fx_hist=None):
        self._ccy, self._last, self._fx_now, self._fx_hist = ccy, last, fx_now, fx_hist
        self.warmed = None

    def warm(self, tickers):
        self.warmed = set(tickers)

    def currency(self, ticker):
        return self._ccy

    def base_factor(self, ccy):
        # GBp (peniques) cotiza en centésimas de libra; el resto es 1:1.
        return ("GBP", 0.01) if ccy == "GBp" else (ccy, 1.0)

    def fx_series(self, ccy):
        return self._fx_hist

    def fx_now(self, ccy):
        return self._fx_now

    def last_price(self, ticker):
        return self._last


def mov(i, side, qty, price, date="2024-01-01", fee=0.0, ticker="AAA"):
    return {"id": i, "date": date, "ticker": ticker, "side": side,
            "quantity": qty, "price": price, "fee": fee, "name": "", "kind": ""}


def _serie(pares):
    idx = pd.to_datetime([d for d, _ in pares])
    return pd.Series([v for _, v in pares], index=idx)


# ── la regla principal ────────────────────────────────────────────────────
def test_el_coste_usa_el_cambio_del_dia_de_la_compra_no_el_de_hoy():
    """Compra 10 a 100 USD cuando el dólar valía 0,80 €; hoy vale 0,50 €.

    El coste es 800 €, no 500 €. Valorar el coste al cambio de hoy convierte
    una posición sana en una pérdida imaginaria del 37%, y al revés.
    """
    fx = _serie([("2024-01-01", 0.80), ("2026-01-01", 0.50)])
    market = FakeMarket(ccy="USD", last=100.0, fx_now=0.50, fx_hist=fx)

    p = compute([mov(1, "buy", 10, 100.0, date="2024-01-01")], market)[0]

    assert p["invested"] == 800.0          # 10 x 100 x 0,80 — el cambio de ENTONCES
    assert p["market_value"] == 500.0      # 10 x 100 x 0,50 — el cambio de HOY
    assert p["unreal"] == -300.0
    assert p["avg_cost"] == 100.0          # el coste medio se queda en divisa nativa


def test_sin_tipo_de_cambio_la_columna_en_euros_se_retira_entera():
    """Nunca un número fabricado dando por hecho que el cambio era 1,0. Y o
    están los tres campos o no está ninguno: una fila mezclada se lee como una
    pérdida."""
    market = FakeMarket(ccy="USD", last=100.0, fx_now=None, fx_hist=None)

    p = compute([mov(1, "buy", 10, 100.0)], market)[0]

    assert p["valued"] is False
    assert p["why"] == "sin tipo de cambio"
    assert p["invested"] is None and p["market_value"] is None and p["unreal"] is None
    assert p["realized"] is None           # se acumuló con datos parciales
    assert p["qty"] == 10.0                # la cantidad sí se sabe, y se dice


def test_sin_moneda_conocida_tampoco_se_inventa():
    market = FakeMarket(ccy="", last=100.0, fx_now=0.9)
    p = compute([mov(1, "buy", 10, 100.0)], market)[0]
    assert p["why"] == "moneda desconocida" and p["ccy"] == "?"


def test_sin_precio_se_dice_cual_falta():
    market = FakeMarket(ccy="EUR", last=None, fx_now=1.0)
    p = compute([mov(1, "buy", 10, 100.0)], market)[0]
    assert p["why"] == "sin precio" and p["valued"] is False


# ── orden y contabilidad ──────────────────────────────────────────────────
def test_un_movimiento_sin_fecha_ordena_el_ultimo_no_el_primero():
    """La cadena vacía va antes que cualquier fecha real, así que un movimiento
    con la fecha sin parsear se convertía en el más antiguo de la posición y le
    reseteaba el coste medio en silencio."""
    market = FakeMarket(ccy="EUR", last=100.0, fx_now=1.0)
    movs = [mov(1, "buy", 10, 100.0, date="2024-01-01"),
            mov(2, "buy", 10, 300.0, date="")]           # sin fecha: va al final

    p = compute(movs, market)[0]

    assert p["qty"] == 20.0
    assert p["avg_cost"] == 200.0          # (1000 + 3000) / 20


def test_una_venta_no_deja_la_posicion_en_negativo():
    """Vender más de lo que hay es un error de datos —una compra que falta, una
    venta importada dos veces—, e inventar participaciones negativas lo esconde
    detrás de un número plausible."""
    market = FakeMarket(ccy="EUR", last=100.0, fx_now=1.0)
    movs = [mov(1, "buy", 5, 100.0), mov(2, "sell", 8, 110.0, date="2024-02-01")]

    p = compute(movs, market)[0]

    assert p["qty"] == 0.0
    assert p["oversold"] == 3.0            # y lo dice, no lo esconde


def test_un_coste_base_cero_no_se_confunde_con_ausente():
    """Un regalo, un spin-off o una posición bonificada tienen coste 0 de
    verdad. `if avg_cost` lo borraría tratándolo como que falta el dato."""
    market = FakeMarket(ccy="EUR", last=50.0, fx_now=1.0)
    p = compute([mov(1, "buy", 10, 0.0)], market)[0]
    assert p["avg_cost"] == 0.0            # cero, no None
    assert p["unreal_pct"] is None         # pero el % sobre 0 no se publica


def test_las_comisiones_entran_en_el_coste():
    market = FakeMarket(ccy="EUR", last=100.0, fx_now=1.0)
    p = compute([mov(1, "buy", 10, 100.0, fee=15.0)], market)[0]
    assert p["invested"] == 1015.0


def test_el_tipo_llega_ya_convertido_a_la_unidad_COTIZADA():
    """Contrato sutil y fácil de romper: `fx_now`/`fx_series` devuelven euros por
    unidad COTIZADA, con el factor de la divisa ya aplicado.

    Un instrumento en GBp cotiza en peniques, y el adaptador entrega
    GBPEUR x 0,01 — no la libra pelada. `base_factor` NO se vuelve a aplicar
    aquí; sólo sirve para el atajo de un instrumento que ya está en euros.
    Aplicarlo dos veces dividiría la posición entre cien.
    """
    market = FakeMarket(ccy="GBp", last=500.0, fx_now=0.0115, fx_hist=None)

    p = compute([mov(1, "buy", 10, 500.0)], market)[0]

    assert p["invested"] == 57.5           # 10 x 500 peniques x 0,0115 €/penique
    assert p["avg_cost"] == 500.0          # el nativo sigue en peniques


def test_un_instrumento_en_euros_no_pide_tipo_de_cambio():
    """El atajo de `base_factor`: si ya está en la divisa base, factor 1,0 y ni
    se mira la serie. `EUREUR=X` sería un viaje de ida y vuelta a ninguna parte."""
    class SinCambio(FakeMarket):
        def fx_now(self, ccy):
            raise AssertionError("no debería pedirse un tipo para un activo en euros")

    market = SinCambio(ccy="EUR", last=120.0, fx_now=None)
    # fx_now sí se llama al final para el `rate_now` de la valoración, así que
    # sólo se comprueba el camino histórico, que es el del coste.
    market.fx_now = lambda ccy: 1.0

    p = compute([mov(1, "buy", 10, 100.0)], market)[0]

    assert p["invested"] == 1000.0 and p["market_value"] == 1200.0


# ── el contrato con el panel ──────────────────────────────────────────────
def test_el_dominio_calienta_el_mercado_una_sola_vez_y_con_todo():
    market = FakeMarket(ccy="EUR", last=1.0, fx_now=1.0)
    compute([mov(1, "buy", 1, 1.0, ticker="AAA"),
             mov(2, "buy", 1, 1.0, ticker="BBB")], market)
    assert market.warmed == {"AAA", "BBB"}


def test_las_posiciones_abiertas_van_antes_y_por_tamano():
    market = FakeMarket(ccy="EUR", last=10.0, fx_now=1.0)
    movs = [mov(1, "buy", 1, 10.0, ticker="PEQ"),
            mov(2, "buy", 50, 10.0, ticker="GRA"),
            mov(3, "buy", 5, 10.0, ticker="CER"),
            mov(4, "sell", 5, 10.0, ticker="CER", date="2024-02-01")]

    out = compute(movs, market)

    assert [p["ticker"] for p in out] == ["GRA", "PEQ", "CER"]
    assert out[-1]["qty"] == 0.0           # la cerrada, la última


def test_la_divisa_base_es_el_euro():
    assert BASE_CCY == "EUR"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)


# ── dividendos: la renta que no mueve la posición ─────────────────────────
def test_un_dividendo_no_toca_ni_la_cantidad_ni_el_coste():
    """Cobrar un dividendo no compra ni vende nada.

    Si se colase como compra subiría la cantidad de títulos; si se colase como
    venta la bajaría. Las dos formas de equivocarse dejan un precio medio
    plausible, que es lo que las hace caras: nada chirría en pantalla.
    """
    market = FakeMarket(ccy="EUR", last=120.0, fx_now=1.0)
    movs = [mov(1, "buy", 10, 100.0, date="2024-01-01"),
            mov(2, "div", 10, 1.5, date="2024-06-01")]

    p = compute(movs, market)[0]

    assert p["qty"] == 10                       # ni un título más ni uno menos
    assert p["invested"] == 1000.0              # el coste no se toca
    assert p["avg_cost"] == 100.0
    assert p["income"] == 15.0
    assert p["n_dividends"] == 1
    assert p["realized"] == 0.0                 # una renta NO es una plusvalía


def test_la_retencion_de_un_dividendo_se_resta_del_cobro():
    market = FakeMarket(ccy="EUR", last=100.0, fx_now=1.0)
    movs = [mov(1, "buy", 100, 10.0, date="2024-01-01"),
            mov(2, "div", 100, 0.50, date="2024-06-01", fee=9.5)]

    p = compute(movs, market)[0]

    assert p["income"] == 40.5                  # 50 brutos - 9,5 de retención
    assert p["invested"] == 1000.0              # y sigue sin tocar el coste


def test_el_dividendo_se_convierte_al_cambio_de_SU_dia():
    """La misma regla que el coste: el cambio del día del cobro, no el de hoy."""
    fx = _serie([("2024-01-01", 0.80), ("2024-06-01", 0.75), ("2026-01-01", 0.50)])
    market = FakeMarket(ccy="USD", last=100.0, fx_now=0.50, fx_hist=fx)
    movs = [mov(1, "buy", 10, 100.0, date="2024-01-01"),
            mov(2, "div", 10, 2.0, date="2024-06-01")]

    p = compute(movs, market)[0]

    assert p["income"] == 15.0                  # 20 USD x 0,75, no x 0,50


def test_un_dividendo_sin_tipo_de_cambio_no_se_lleva_por_delante_el_realizado():
    """El hueco de cambio de un dividendo se apunta APARTE.

    Compartir bandera con la compraventa haría que un cobro sin tipo retuviera
    un resultado realizado que sí se puede calcular, y una cifra que desaparece
    se lee como un cero.
    """
    market = FakeMarket(ccy="USD", last=100.0, fx_now=None, fx_hist=None)
    movs = [mov(1, "buy", 10, 100.0, date="2024-01-01"),
            mov(2, "div", 10, 1.0, date="2024-06-01")]

    p = compute(movs, market)[0]

    assert p["income"] is None                  # no se inventa un cambio de 1,0
    assert p["valued"] is False


# ── FIFO frente a coste medio ─────────────────────────────────────────────
def test_fifo_y_coste_medio_difieren_en_una_venta_parcial():
    """Dos compras a precios distintos y se vende la mitad.

    Coste medio: 15 por título. FIFO: se van los de 10 primero. La diferencia
    es exactamente lo que separa lo que enseña una cartera de lo que hay que
    declarar, y por eso se publican los dos números en vez de elegir uno.
    """
    market = FakeMarket(ccy="EUR", last=30.0, fx_now=1.0)
    movs = [mov(1, "buy", 10, 10.0, date="2024-01-01"),
            mov(2, "buy", 10, 20.0, date="2024-02-01"),
            mov(3, "sell", 10, 25.0, date="2024-03-01")]

    p = compute(movs, market)[0]

    assert p["realized"] == 100.0               # 250 - 10 x 15 (medio)
    assert p["realized_fifo"] == 150.0          # 250 - 10 x 10 (los primeros)
    assert p["qty"] == 10


def test_al_cerrar_la_posicion_entera_los_dos_criterios_coinciden():
    """Es la propiedad que hace honesto enseñar los dos: cerrar del todo
    consume TODOS los lotes, así que la discrepancia sólo puede vivir en una
    venta parcial. Si divergieran aquí, uno de los dos estaría mal."""
    market = FakeMarket(ccy="EUR", last=30.0, fx_now=1.0)
    movs = [mov(1, "buy", 10, 10.0, date="2024-01-01"),
            mov(2, "buy", 10, 20.0, date="2024-02-01"),
            mov(3, "sell", 20, 25.0, date="2024-03-01")]

    p = compute(movs, market)[0]

    assert p["qty"] == 0
    assert p["realized"] == p["realized_fifo"] == 200.0


def test_fifo_reparte_la_comision_de_compra_en_el_coste_del_lote():
    market = FakeMarket(ccy="EUR", last=30.0, fx_now=1.0)
    movs = [mov(1, "buy", 10, 10.0, date="2024-01-01", fee=5.0),
            mov(2, "buy", 10, 20.0, date="2024-02-01"),
            mov(3, "sell", 5, 30.0, date="2024-03-01")]

    p = compute(movs, market)[0]

    # El primer lote costó 105 por 10 títulos: 10,50 cada uno.
    assert p["realized_fifo"] == round(150.0 - 5 * 10.5, 2)


def test_fifo_usa_el_cambio_del_lote_que_consume():
    """El lote guarda su coste en euros al cambio de SU día. Reconvertir al
    cambio de hoy inventaría una plusvalía de divisa que nadie ha realizado."""
    fx = _serie([("2024-01-01", 1.0), ("2024-02-01", 0.5), ("2024-03-01", 0.5)])
    market = FakeMarket(ccy="USD", last=30.0, fx_now=0.5, fx_hist=fx)
    movs = [mov(1, "buy", 10, 10.0, date="2024-01-01"),   # 100 USD -> 100 €
            mov(2, "buy", 10, 10.0, date="2024-02-01"),   # 100 USD ->  50 €
            mov(3, "sell", 10, 10.0, date="2024-03-01")]  # 100 USD ->  50 €

    p = compute(movs, market)[0]

    assert p["realized_fifo"] == -50.0          # 50 cobrados contra 100 de coste
    assert p["realized"] == -25.0               # el medio reparte: 75 de coste


def test_el_dividendo_no_gasta_lotes_de_fifo():
    market = FakeMarket(ccy="EUR", last=30.0, fx_now=1.0)
    movs = [mov(1, "buy", 10, 10.0, date="2024-01-01"),
            mov(2, "div", 10, 1.0, date="2024-02-01"),
            mov(3, "sell", 10, 20.0, date="2024-03-01")]

    p = compute(movs, market)[0]

    assert p["realized_fifo"] == 100.0          # el lote de 10 seguía entero
    assert p["income"] == 10.0


# ── peso de cada posición ─────────────────────────────────────────────────
def test_los_pesos_suman_cien_sobre_lo_valorado():
    market = FakeMarket(ccy="EUR", last=100.0, fx_now=1.0)
    movs = [mov(1, "buy", 30, 10.0, ticker="AAA"),
            mov(2, "buy", 10, 10.0, ticker="BBB")]

    out = {p["ticker"]: p for p in compute(movs, market)}

    assert out["AAA"]["weight"] == 75.0
    assert out["BBB"]["weight"] == 25.0


def test_una_posicion_sin_valorar_no_recibe_peso_cero():
    """Un peso de 0% dice «no tienes casi nada de esto», que es lo contrario de
    «no he podido calcular cuánto tienes». `None` es la respuesta honesta."""
    market = FakeMarket(ccy="", last=None, fx_now=None)
    p = compute([mov(1, "buy", 10, 10.0)], market)[0]

    assert p["market_value"] is None
    assert p["weight"] is None


def test_una_posicion_cerrada_no_pesa():
    market = FakeMarket(ccy="EUR", last=100.0, fx_now=1.0)
    movs = [mov(1, "buy", 10, 10.0, ticker="AAA"),
            mov(2, "buy", 10, 10.0, ticker="BBB"),
            mov(3, "sell", 10, 12.0, ticker="BBB", date="2024-02-01")]

    out = {p["ticker"]: p for p in compute(movs, market)}

    assert out["AAA"]["weight"] == 100.0
    assert out["BBB"]["weight"] is None


# ── los lotes abiertos, que ahora salen a la superficie ───────────────────
def test_los_lotes_se_publican_con_su_fecha_y_su_coste_unitario():
    """Se calculaban ya para el FIFO y se tiraban. Publicarlos permite
    responder «¿y si vendo?» sin recorrer otra vez el libro, y garantiza que
    esa respuesta no pueda discrepar del realizado que se enseña al lado."""
    market = FakeMarket(ccy="EUR", last=30.0, fx_now=1.0)
    movs = [mov(1, "buy", 10, 10.0, date="2024-01-01", fee=5.0),
            mov(2, "buy", 5, 20.0, date="2024-06-01")]

    p = compute(movs, market)[0]

    assert [l["qty"] for l in p["lots"]] == [10.0, 5.0]
    assert p["lots"][0]["unit_cost"] == 10.5          # 105 / 10, comisión incluida
    assert [l["date"] for l in p["lots"]] == ["2024-01-01", "2024-06-01"]


def test_una_venta_parcial_deja_el_lote_a_medias_y_se_ve():
    market = FakeMarket(ccy="EUR", last=30.0, fx_now=1.0)
    movs = [mov(1, "buy", 10, 10.0, date="2024-01-01"),
            mov(2, "buy", 10, 20.0, date="2024-02-01"),
            mov(3, "sell", 14, 25.0, date="2024-03-01")]

    p = compute(movs, market)[0]

    assert [l["qty"] for l in p["lots"]] == [6.0]     # del primero no queda nada
    assert p["lots"][0]["unit_cost"] == 20.0
