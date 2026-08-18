"""Lo que faltaba para poder llevar una cartera de verdad: dividendos, corregir
un movimiento, y saber en qué zona está lo que tienes.

Cada caso de aquí corresponde a una carencia que producía un NÚMERO EQUIVOCADO
o una pérdida de datos, no un error visible:

  * un dividendo apuntado como compraventa mueve títulos que nadie movió;
  * corregir un precio obligaba a BORRAR el movimiento, sobre el único estado
    que esta aplicación no puede reconstruir;
  * el resultado realizado se enseñaba con un solo criterio de coste sin decir
    cuál, y no es el que pide la declaración.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


@pytest.fixture
def libro(tmp_path, monkeypatch):
    """Un libro vacío y un mercado fijado: sólo varía la contabilidad."""
    db = str(tmp_path / "cartera.db")
    monkeypatch.setattr(D, "CARTERA_DB", db)
    monkeypatch.setattr(D, "_instrument_ccy", lambda s: "EUR")
    monkeypatch.setattr(D, "_fx_series_eur", lambda c: None)
    monkeypatch.setattr(D, "_fx_now", lambda c: 1.0)
    monkeypatch.setattr(D, "_last_price", lambda t: 120.0)
    monkeypatch.setattr(D, "_seed_geo_async", lambda *a, **k: None)
    monkeypatch.setattr(D, "PUBLIC_HOST", "")
    return D.app.test_client()


def _post(cli, **body):
    r = cli.post("/api/cartera", json=body, headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _rows(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT id,date,ticker,side,quantity,price,fee,note "
                           "FROM movements ORDER BY id").fetchall()
    finally:
        con.close()


# ── vocabulario del movimiento ────────────────────────────────────────────
def test_el_extracto_de_un_banco_nombra_el_dividendo_de_muchas_formas():
    for v in ("Dividendo", "DIVIDENDO BRUTO", "Abono dividendo", "Pago de cupón",
              "dividend", "coupon", "Reparto", "Intereses", "d"):
        assert D._norm_side(v, 10) == "div", v


def test_la_renta_gana_a_la_palabra_que_dice_por_donde_entro_el_dinero():
    """«Abono dividendo» lleva DOS palabras del vocabulario: `abono`, que es una
    venta, y `dividendo`. Sin prioridad explícita ganaba la venta, y el cobro se
    comía participaciones que nadie había vendido."""
    assert D._norm_side("Abono dividendo", 10) == "div"
    assert D._norm_side("Abono", 10) == "sell"          # a secas sigue siendo venta


def test_un_dividendo_nunca_sale_del_signo_de_la_cantidad():
    """Es el único movimiento que no mueve la posición: hay que nombrarlo. Que
    lo dedujera un signo lo convertiría en el destino de cualquier fila rara."""
    assert D._norm_side("", 10) == "buy"
    assert D._norm_side("Sarasa", -10) == "sell"


# ── el dividendo, de punta a punta ────────────────────────────────────────
def test_un_dividendo_no_mueve_la_posicion_y_va_a_su_propia_cifra(libro):
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    p = _post(libro, ticker="AAA", side="div", quantity=10, price=1.5, date="2024-06-01")

    pos = p["positions"][0]
    assert pos["qty"] == 10
    assert pos["invested"] == 1000.0
    assert pos["income"] == 15.0
    assert p["summary"]["income"] == 15.0
    assert p["summary"]["n_dividends"] == 1
    assert p["summary"]["realized"] == 0.0


def test_sin_titulos_el_dividendo_se_apunta_por_el_importe(libro):
    """Un extracto suele dar el total cobrado y no siempre el importe por
    título. Exigir la cantidad obligaría a inventársela."""
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    p = _post(libro, ticker="AAA", side="div", price=45.20, date="2024-06-01")

    assert p["positions"][0]["income"] == 45.20
    assert p["positions"][0]["qty"] == 10


def test_la_rentabilidad_total_suma_las_tres_piezas(libro):
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="div", price=30, date="2024-06-01")
    p = _post(libro, ticker="AAA", side="sell", quantity=5, price=110, date="2024-07-01")

    s = p["summary"]
    assert s["realized"] == 50.0                       # 5 x (110 - 100)
    assert s["income"] == 30.0
    assert s["unreal"] == 100.0                        # 5 x (120 - 100)
    assert s["total_return"] == 180.0


def test_el_dividendo_viaja_en_el_csv_como_palabra_completa(libro):
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="div", quantity=10, price=1.5, date="2024-06-01")

    csv = libro.get("/api/cartera/export").get_data(as_text=True)

    assert "dividendo" in csv
    # Y vuelve a entrar valiendo lo mismo: el viaje de ida y vuelta es la regla
    # que gobierna la exportación entera.
    filas, errores, _det = D._parse_upload("x.csv", csv.encode("utf-8"))
    assert errores == []
    assert [f["side"] for f in filas] == ["buy", "div"]


def test_el_dividendo_no_entra_en_la_reconstruccion_del_grafico(libro, monkeypatch):
    """La evolución sólo sabe de títulos que entran y dinero que se despliega.

    Un dividendo no es ninguna de las dos, y cayendo por el `else` se habría
    tratado como VENTA: le habría restado títulos a la posición y habría sacado
    del benchmark un dinero que nunca se retiró.
    """
    visto = []
    monkeypatch.setattr(D, "_close_series", lambda t: None)
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: visto.append(list(ts)))
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="div", quantity=10, price=1.5, date="2024-06-01")

    h = D._cartera_history("SPY")

    assert h["portfolio"] == []          # sin series no hay gráfico, y no revienta
    assert h["covered"] is False


# ── corregir en vez de borrar ─────────────────────────────────────────────
def test_corregir_el_precio_no_borra_el_movimiento(libro, tmp_path):
    p = _post(libro, ticker="AAA", side="buy", quantity=10, price=1000, date="2024-01-01")
    mid = p["movements"][0]["id"]

    r = libro.patch(f"/api/cartera/{mid}", json={"price": 100},
                    headers={D.CSRF_HEADER: "1"})

    assert r.status_code == 200
    filas = _rows(str(tmp_path / "cartera.db"))
    assert len(filas) == 1                        # sigue habiendo UN movimiento
    assert filas[0][5] == 100.0
    assert r.get_json()["positions"][0]["invested"] == 1000.0


def test_lo_que_no_viaja_en_el_cuerpo_se_queda_como_estaba(libro, tmp_path):
    p = _post(libro, ticker="AAA", side="buy", quantity=10, price=100,
              date="2024-01-01", fee=2.5, note="original")
    mid = p["movements"][0]["id"]

    libro.patch(f"/api/cartera/{mid}", json={"price": 110}, headers={D.CSRF_HEADER: "1"})

    _id, date, tk, side, q, px, fee, note = _rows(str(tmp_path / "cartera.db"))[0]
    assert (date, tk, side, q, px, fee, note) == ("2024-01-01", "AAA", "buy", 10.0, 110.0, 2.5, "original")


def test_un_campo_presente_pero_ilegible_es_un_error_no_un_cero(libro):
    p = _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    mid = p["movements"][0]["id"]

    r = libro.patch(f"/api/cartera/{mid}", json={"price": "abc"},
                    headers={D.CSRF_HEADER: "1"})

    assert r.status_code == 400
    assert "precio" in r.get_json()["error"]


def test_cambiar_de_lado_reinterpreta_la_cantidad_de_la_MISMA_edicion(libro, tmp_path):
    """Si en la misma corrección llegan lado y cantidad, manda la cantidad
    nueva. Usar la vieja para desempatar sería leer el signo de un movimiento
    que ya no existe."""
    p = _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    mid = p["movements"][0]["id"]

    libro.patch(f"/api/cartera/{mid}", json={"side": "Sarasa", "quantity": -4},
                headers={D.CSRF_HEADER: "1"})

    _id, _d, _t, side, q, *_ = _rows(str(tmp_path / "cartera.db"))[0]
    assert side == "sell"
    assert q == 4.0                               # se guarda en positivo


def test_cambiar_de_instrumento_no_hereda_el_nombre_viejo(libro, tmp_path):
    """Una fila que dice una cosa y vale otra es peor que una fila sin nombre."""
    p = _post(libro, ticker="AAA", side="buy", quantity=10, price=100, name="Antigua SA")
    mid = p["movements"][0]["id"]

    libro.patch(f"/api/cartera/{mid}", json={"ticker": "BBB"}, headers={D.CSRF_HEADER: "1"})

    con = sqlite3.connect(str(tmp_path / "cartera.db"))
    tk, name = con.execute("SELECT ticker,name FROM movements").fetchone()
    con.close()
    assert (tk, name) == ("BBB", "")


def test_corregir_un_movimiento_que_ya_no_existe_da_404(libro):
    r = libro.patch("/api/cartera/9999", json={"price": 1}, headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 404


def test_corregir_exige_la_cabecera_como_cualquier_otra_escritura(libro):
    p = _post(libro, ticker="AAA", side="buy", quantity=10, price=100)
    mid = p["movements"][0]["id"]

    r = libro.patch(f"/api/cartera/{mid}", json={"price": 1})

    assert r.status_code == 403


# ── la zona de cada posición ──────────────────────────────────────────────
def test_las_zonas_salen_del_libro_y_no_de_la_peticion(libro, monkeypatch):
    """Los símbolos los pone la cartera. Aceptarlos por parámetro convertiría
    esta ruta en un barredor de tickers ajenos con la caché del panel detrás."""
    pedidos = []
    monkeypatch.setattr(D, "_zone_of", lambda s: pedidos.append(s) or
                        {"zone": "Capitulación", "score": 12.0, "dwell": 3,
                         "close": 90.0, "model": "full", "date": "2026-08-18"})
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)

    d = libro.get("/api/cartera/zonas?symbol=EVIL").get_json()

    assert pedidos == ["AAA"]
    assert d["zones"]["AAA"]["zone"] == "Capitulación"
    assert d["pending"] == []


def test_una_posicion_ya_cerrada_no_pide_zona(libro, monkeypatch):
    pedidos = []
    monkeypatch.setattr(D, "_zone_of", lambda s: pedidos.append(s) or
                        {"zone": "Euforia", "score": 90.0, "dwell": 1,
                         "close": 1.0, "model": "full", "date": "2026-08-18"})
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="sell", quantity=10, price=110, date="2024-02-01")

    libro.get("/api/cartera/zonas")

    assert pedidos == []


def test_un_dividendo_no_cuenta_como_titulos_al_listar_zonas(libro, monkeypatch):
    monkeypatch.setattr(D, "_zone_of", lambda s: {"zone": "Equilibrio", "score": 50.0,
                                                  "dwell": 1, "close": 1.0,
                                                  "model": "full", "date": "2026-08-18"})
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100, date="2024-01-01")
    _post(libro, ticker="AAA", side="sell", quantity=10, price=110, date="2024-02-01")
    _post(libro, ticker="AAA", side="div", price=5, date="2024-03-01")

    d = libro.get("/api/cartera/zonas").get_json()

    assert d["zones"] == {}          # la posición está cerrada, dividendo aparte


def test_un_instrumento_sin_historico_se_dice_con_su_nombre(libro, monkeypatch):
    def sin_historico(s):
        raise D.NoHistory("nada que puntuar")
    monkeypatch.setattr(D, "_zone_of", sin_historico)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)

    d = libro.get("/api/cartera/zonas").get_json()

    assert "error" in d["zones"]["AAA"]
    assert d["pending"] == []                     # asentado: no se reintenta


def test_un_tropiezo_pasajero_vuelve_a_la_cola(libro, monkeypatch):
    """Un fallo de red no puede quedar cacheado como «este activo no tiene
    zona»: el cliente vuelve a preguntar y la casilla se rellena sola."""
    def revienta(s):
        raise OSError("yahoo se cayó")
    monkeypatch.setattr(D, "_zone_of", revienta)
    _post(libro, ticker="AAA", side="buy", quantity=10, price=100)

    d = libro.get("/api/cartera/zonas").get_json()

    assert d["zones"] == {}
    assert d["pending"] == ["AAA"]


# ── en qué moneda se teclea el precio ─────────────────────────────────────
def test_el_panel_dice_la_divisa_del_instrumento(libro, monkeypatch):
    monkeypatch.setattr(D, "_quote_meta", lambda s: (412.5, "USD"))

    d = libro.get("/api/instrumento?symbol=SPY").get_json()

    assert d["ccy"] == "USD"
    assert d["factor"] == 1.0


def test_una_plaza_en_peniques_se_avisa_aparte(libro, monkeypatch):
    """GBp cotiza en centésimas de libra. El error no es del 20%: es de 100x."""
    monkeypatch.setattr(D, "_quote_meta", lambda s: (850.0, "GBp"))

    d = libro.get("/api/instrumento?symbol=VOD.L").get_json()

    assert d["ccy"] == "GBp"
    assert d["base_ccy"] == "GBP"
    assert d["factor"] == 0.01
