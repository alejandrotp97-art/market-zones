"""El índice con el que se compara tiene que mandar en TODO lo que se compara.

Cambiarlo movía sólo el gráfico: el estado, la rentabilidad, la beta y la
correlación por posición seguían midiendo contra SPY con el nombre del otro
índice escrito encima. Esto fija el CABLEADO —quién recibe el índice elegido—,
no las fórmulas, que viven en `tests/test_returns.py`.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


@pytest.fixture
def libro(tmp_path, monkeypatch):
    db = str(tmp_path / "cartera.db")
    monkeypatch.setattr(D, "CARTERA_DB", db)
    monkeypatch.setattr(D, "_instrument_ccy", lambda s: "EUR")
    monkeypatch.setattr(D, "_fx_series_eur", lambda c: None)
    monkeypatch.setattr(D, "_fx_now", lambda c: 1.0)
    monkeypatch.setattr(D, "_last_price", lambda t: 120.0)
    monkeypatch.setattr(D, "_seed_geo_async", lambda *a, **k: None)
    monkeypatch.setattr(D, "PUBLIC_HOST", "")
    monkeypatch.setattr(D, "_close_series",
                        lambda t: pd.Series([100.0 + i * 0.1 for i in range(300)],
                                            index=pd.bdate_range("2024-01-01", periods=300)))
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    return D.app.test_client()


def _post(cli, **body):
    r = cli.post("/api/cartera", json=body, headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _una_posicion(cli):
    _post(cli, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-02")


def test_el_estado_pasa_su_indice_a_la_correlacion(libro, monkeypatch):
    """El `eff_n_corr` del estado alimenta el aviso de las «apuestas
    independientes», y la tarjeta de Diversificación calcula el suyo con el
    índice elegido. Si el estado se queda en SPY, la misma cuenta sale con dos
    números en la misma pantalla y nada dice cuál mirar."""
    _una_posicion(libro)
    visto = {}

    def espia(days=D.CORR_DAYS, benchmark="SPY", payload=None):
        visto["benchmark"] = benchmark
        return {"eff_n_corr": 1.0, "eff_n_weights": 1.0}

    monkeypatch.setattr(D, "_cartera_correlacion", espia)

    D._cartera_estado("URTH")

    assert visto["benchmark"] == "URTH"


def test_el_estado_sin_indice_sigue_midiendo_contra_SPY(libro, monkeypatch):
    """`api_cartera_plan` llama sin argumento. El valor por defecto es contrato,
    no casualidad: quitarlo dejaría esa ruta sin índice ninguno."""
    _una_posicion(libro)
    visto = {}

    def espia(days=D.CORR_DAYS, benchmark="SPY", payload=None):
        visto["benchmark"] = benchmark
        return {"eff_n_corr": 1.0, "eff_n_weights": 1.0}

    monkeypatch.setattr(D, "_cartera_correlacion", espia)

    D._cartera_estado()

    assert visto["benchmark"] == "SPY"


def test_la_ruta_del_estado_respeta_el_indice_pedido(libro):
    """Lo que la pantalla manda por la URL tiene que llegar entero: sin esto la
    casilla «Frente a X» decía un nombre y medía contra otro."""
    _una_posicion(libro)

    r = libro.get("/api/cartera/estado?benchmark=URTH")

    assert r.status_code == 200
    assert r.get_json()["benchmark_ticker"] == "URTH"


def test_un_indice_que_no_se_puede_descargar_no_tumba_la_tarjeta(libro, monkeypatch):
    """Sin serie del índice no hay comparación, pero el resto de la tarjeta —lo
    que es TUYO— tiene que seguir ahí. Enseñar una tarjeta vacía porque falló un
    índice sería castigar al usuario por elegir mal el punto de comparación."""
    _una_posicion(libro)
    real = D._close_series
    monkeypatch.setattr(D, "_close_series", lambda t: None if t == "NOEXISTE" else real(t))

    e = D._cartera_estado("NOEXISTE")

    assert e["n_positions"] == 1
    assert e["benchmark_ticker"] == "NOEXISTE"
    assert e["vs_benchmark"] is None


def test_la_rejilla_declarada_es_la_del_indice_cuando_hay_serie(libro):
    """La tarjeta anuncia sobre qué sesiones está medida. Si el índice se pudo
    descargar, la rejilla es la suya y se le nombra."""
    _una_posicion(libro)

    e = D._cartera_estado("URTH")

    assert e["sessions_ticker"] == "URTH"


def test_sin_serie_del_indice_no_se_le_atribuyen_las_sesiones(libro, monkeypatch):
    """Sin serie del índice, la rejilla cae a los calendarios de las propias
    posiciones. Seguir anunciando «sesiones de X» nombraría una fuente que nunca
    se uso, y es justo la clase de discrepancia que este aviso existe para
    evitar. `None` es lo que hace que la pantalla diga «de tus posiciones»."""
    _una_posicion(libro)
    real = D._close_series
    monkeypatch.setattr(D, "_close_series", lambda t: None if t == "NOEXISTE" else real(t))

    e = D._cartera_estado("NOEXISTE")

    assert e["benchmark_ticker"] == "NOEXISTE"     # lo que pediste, se respeta
    assert e["sessions_ticker"] is None            # de dónde salió, no se inventa
