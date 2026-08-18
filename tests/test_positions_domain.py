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
