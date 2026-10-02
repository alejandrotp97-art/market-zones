"""Tickers escritos a mano: el nombre que tiene delante, no el símbolo de Yahoo.

`movements.ticker = 'BITCOIN EUR'` llegó al libro como texto libre —uno escribe
lo que ve en el extracto— y ese texto no es un símbolo: `safe_symbol` lo
rechaza, la posición se queda sin divisa y cae de todos los totales, de la
historia, de la correlación, de las clases y del mapa. Lo mismo vale para
cualquier nombre con espacios tecleado en el formulario o metido en un CSV.

Aquí se fija el arreglo en sus dos mitades: la tabla de alias local, que se
consulta ANTES de cualquier llamada a la red (buscar «BITCOIN EUR» en Yahoo
devuelve primero un contrato de FUTUROS de microbitcoin, que es justamente lo
que nadie pidió), y la traducción en cada puerta de entrada —alta, edición y
CSV—, más la migración de las filas que ya están guardadas: una copia de
seguridad restaurada trae las mismas.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


def _sin_red(monkeypatch):
    """Cualquier búsqueda tiene que reventar: el alias no debe pedir ninguna."""
    def revienta(*args, **kwargs):
        raise AssertionError("esta consulta no debería salir a la red")
    monkeypatch.setattr(D, "_yahoo_search", revienta)


@pytest.fixture
def libro(tmp_path, monkeypatch):
    """Libro en un fichero temporal y mercado fijado: sólo varía la traducción
    del ticker, que es lo único que estos tests quieren ver moverse."""
    monkeypatch.setattr(D, "CARTERA_DB", str(tmp_path / "cartera.db"))
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_fx_series_eur", lambda c: None)
    monkeypatch.setattr(D, "_fx_now", lambda c: 1.0)
    monkeypatch.setattr(D, "_seed_geo_async", lambda *a, **k: None)
    monkeypatch.setattr(D, "PUBLIC_HOST", "")
    return D.app.test_client()


def _post(cli, **body):
    r = cli.post("/api/cartera", json=body, headers={D.CSRF_HEADER: "1"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


# ── la tabla local ───────────────────────────────────────────────────────
def test_el_alias_se_resuelve_sin_salir_a_la_red(monkeypatch):
    """Sin el alias la posición no tiene divisa y se queda fuera de todo, así
    que tiene que responder con la misma tabla local aunque Yahoo esté caído."""
    _sin_red(monkeypatch)
    assert D._resolve_symbol("BITCOIN EUR") == ("BTC-EUR", "Bitcoin EUR", "Cripto")
    assert D._resolve_symbol("  bitcoin eur  ") == ("BTC-EUR", "Bitcoin EUR", "Cripto")


def test_el_nombre_exacto_gana_al_primer_resultado_de_yahoo(monkeypatch):
    """El segundo mecanismo, por si mañana aparece otro nombre sin alias: Yahoo
    devuelve el FUTUROS de microbitcoin PRIMERO porque es lo que más se mueve, y
    `res[0]` apuntaría un derivado donde el usuario guardaba su ahorro."""
    monkeypatch.setattr(D, "SYMBOL_ALIASES", {})
    monkeypatch.setattr(D, "_yahoo_search", lambda q, limit=12: [
        {"symbol": "EBM=F", "name": "Micro Bitcoin Euro Futures", "kind": "Futuro"},
        {"symbol": "BTC-EUR", "name": "Bitcoin EUR", "kind": "Cripto"},
    ])
    assert D._resolve_symbol("BITCOIN EUR") == ("BTC-EUR", "Bitcoin EUR", "Cripto")


def test_un_ticker_de_una_palabra_no_paga_búsqueda(monkeypatch):
    """Un código sin espacios ni forma de ISIN ES el símbolo: subirlo a
    mayúsculas es toda la traducción que necesita, y salir a la red para eso
    convertiría cada alta en una petición a un tercero."""
    _sin_red(monkeypatch)
    assert D._resolve_symbol("BTC-EUR") == ("BTC-EUR", "", "")
    assert D._resolve_symbol("spy") == ("SPY", "", "")


# ── en cada puerta de entrada ────────────────────────────────────────────
def test_el_csv_con_texto_libre_se_guarda_como_simbolo(monkeypatch):
    """La columna de ticker de un extracto trae nombres, no códigos. Guardar el
    nombre tal cual dejaba la fila sin divisa y fuera de los totales."""
    _sin_red(monkeypatch)
    csv = ("Fecha,Ticker,Cantidad,Precio\n"
           "01/01/2024,BITCOIN EUR,1,70000\n")

    filas, errores, _ = D._parse_upload("x.csv", csv.encode())

    assert errores == []
    assert filas[0]["ticker"] == "BTC-EUR"
    assert filas[0]["name"] == "Bitcoin EUR"
    assert filas[0]["kind"] == "Cripto"


def test_el_alta_por_el_formulario_guarda_el_simbolo_resuelto(libro, tmp_path,
                                                              monkeypatch):
    _sin_red(monkeypatch)

    p = _post(libro, ticker="BITCOIN EUR", side="buy", quantity=1, price=70000,
              date="2024-01-01")

    assert p["positions"][0]["ticker"] == "BTC-EUR"
    con = sqlite3.connect(str(tmp_path / "cartera.db"))
    try:
        fila = con.execute("SELECT ticker,name,kind FROM movements").fetchone()
    finally:
        con.close()
    assert fila == ("BTC-EUR", "Bitcoin EUR", "Cripto")


def test_corregir_el_ticker_tambien_lo_resuelve(libro, tmp_path, monkeypatch):
    """La edición es la tercera puerta: si sólo se arreglaba el alta, el ✎ de la
    página de movimientos volvía a meter el texto libre en el libro."""
    _sin_red(monkeypatch)
    mid = _post(libro, ticker="AAA", side="buy", quantity=1, price=100)["movements"][0]["id"]

    r = libro.patch(f"/api/cartera/{mid}", json={"ticker": "BITCOIN EUR"},
                    headers={D.CSRF_HEADER: "1"})

    assert r.status_code == 200, r.get_data(as_text=True)
    con = sqlite3.connect(str(tmp_path / "cartera.db"))
    try:
        fila = con.execute("SELECT ticker,name FROM movements").fetchone()
    finally:
        con.close()
    assert fila == ("BTC-EUR", "Bitcoin EUR")


# ── la migración de lo que ya está guardado ──────────────────────────────
@pytest.fixture
def viejo(tmp_path, monkeypatch):
    """Un libro poblado con texto libre ANTES de que existiera el alias."""
    monkeypatch.setattr(D, "CARTERA_DB", str(tmp_path / "cartera.db"))
    with D._cartera_conn():                  # sólo el esquema: aún no hay filas
        pass
    con = sqlite3.connect(D.CARTERA_DB)
    try:
        con.executemany(
            "INSERT INTO movements(date,ticker,name,kind,side,quantity,price,fee) "
            "VALUES(?,?,?,?,?,?,?,0)",
            [("2024-01-01", "BITCOIN EUR", "", "", "buy", 1, 70000),
             ("2024-02-01", "BITCOIN EUR", "", "", "buy", 1, 60000),
             # Con nombre propio no hay que inventarle uno en la migración.
             ("2024-03-01", "BITCOIN EUR", "Mi bitcoin", "", "buy", 1, 50000)])
        con.execute("INSERT INTO instrument_meta(ticker,ter) VALUES('BITCOIN EUR', 0.25)")
        con.commit()
    finally:
        con.close()
    return D.CARTERA_DB


def test_las_filas_viejas_se_traducen_y_se_rellenan(viejo):
    with D._cartera_conn() as c:
        filas = c.execute("SELECT ticker,name,kind FROM movements ORDER BY id").fetchall()
        meta = c.execute("SELECT ticker,ter FROM instrument_meta").fetchall()

    assert [f[0] for f in filas] == ["BTC-EUR"] * 3
    # Las dos vacías se completan con lo que dice el alias...
    assert filas[0] == ("BTC-EUR", "Bitcoin EUR", "Cripto")
    assert filas[1] == ("BTC-EUR", "Bitcoin EUR", "Cripto")
    # ...y la que ya tenía nombre se queda con él: la migración rellena huecos.
    assert filas[2] == ("BTC-EUR", "Mi bitcoin", "")
    assert meta == [("BTC-EUR", 0.25)]


def test_la_migracion_se_puede_ejecutar_dos_veces(viejo):
    """Se corre en CADA conexión, así que tiene que ser idempotente: la segunda
    pasada ya no encuentra el texto viejo y no vuelve a tocar nada."""
    for _ in range(2):
        with D._cartera_conn() as c:
            viejos = c.execute(
                "SELECT COUNT(*) FROM movements WHERE ticker='BITCOIN EUR'").fetchone()[0]
            meta_viejo = c.execute(
                "SELECT COUNT(*) FROM instrument_meta WHERE ticker='BITCOIN EUR'").fetchone()[0]
            nuevos = c.execute(
                "SELECT COUNT(*) FROM movements WHERE ticker='BTC-EUR'").fetchone()[0]
            meta_nuevo = c.execute(
                "SELECT COUNT(*) FROM instrument_meta WHERE ticker='BTC-EUR'").fetchone()[0]
        assert (viejos, meta_viejo, nuevos, meta_nuevo) == (0, 0, 3, 1)


def test_si_el_destino_ya_tiene_ficha_gana_la_del_destino(viejo):
    """Las dos filas a la vez no pueden quedarse: `instrument_meta` tiene el
    ticker como clave y renombrar sobre una fila que ya existe reventaría con
    una violación de clave primaria. Lo que manda es lo que ya estaba en el
    símbolo bueno, que es donde la ficha se escribió sabiendo de qué iba."""
    con = sqlite3.connect(viejo)
    try:
        con.execute("INSERT INTO instrument_meta(ticker,ter) VALUES('BTC-EUR', 0.19)")
        con.commit()
    finally:
        con.close()

    with D._cartera_conn() as c:
        meta = c.execute("SELECT ticker,ter FROM instrument_meta ORDER BY ticker").fetchall()

    assert meta == [("BTC-EUR", 0.19)]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
