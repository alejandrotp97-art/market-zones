"""Salidas de la cartera: exportación y copia de seguridad.

El libro de movimientos es el único dato del proyecto que no se puede volver a
calcular. Estos tests cubren las dos únicas maneras que hay de sacarlo de la
máquina, y cada uno corresponde a una forma de perderlo:

  - exportar un fichero que luego NO se puede reimportar,
  - guardar una copia que nadie ha abierto nunca y que resulta estar rota,
  - guardar la copia con permisos que la dejan leer a cualquiera,
  - gastar la retención en copias idénticas y perder el estado de hace un mes.
"""
import os
import sqlite3
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup_cartera as B
import dashboard as D


def _fresh_db():
    """Una base vacía con el esquema real del panel."""
    path = os.path.join(tempfile.mkdtemp(), "cartera.db")
    real = D.CARTERA_DB
    D.CARTERA_DB = path
    try:
        with D._cartera_conn():
            pass
    finally:
        D.CARTERA_DB = real
    return path


def _insert(path, rows):
    con = sqlite3.connect(path)
    with con:
        con.executemany(
            "INSERT INTO movements(date,ticker,name,kind,side,quantity,price,fee,note) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows)
    con.close()


def _export_bytes(db_path):
    real = D.CARTERA_DB
    D.CARTERA_DB = db_path
    try:
        r = D.app.test_client().get("/api/cartera/export")
    finally:
        D.CARTERA_DB = real
    return r


# ── exportación ───────────────────────────────────────────────────────────
def test_export_round_trips_through_the_importer():
    """El test que justifica la función: lo exportado se vuelve a importar.

    Un export que el importador no acepta no es una copia de tu libro, es un
    informe bonito. Se prueba con las filas que rompen un CSV ingenuo: una coma
    y unas comillas dentro de la nota, acentos en el nombre, una venta, una
    comisión y un movimiento sin fecha.
    """
    db = _fresh_db()
    _insert(db, [
        ("2024-01-15", "SPY", "SPDR S&P 500", "ETF", "buy", 10.0, 450.2, 1.5,
         "compra inicial, tramo 1"),
        ("2024-06-01", "SPY", "SPDR S&P 500", "ETF", "sell", 4.0, 510.0, 1.5,
         'toma parcial "planificada"'),
        ("", "0P0000XYZ.F", "Fondo Índice Acciones España", "Fondo", "buy",
         12.345, 18.9, 0.0, ""),
    ])
    raw = _export_bytes(db).data

    rows, errors, detected = D._parse_upload("cartera.csv", raw)

    assert errors == [], errors
    assert len(rows) == 3
    assert {"date", "ticker", "side", "quantity", "price", "fee", "note", "name"} <= set(detected)

    by_note = {r["note"]: r for r in rows}
    a = by_note["compra inicial, tramo 1"]           # la coma no partió la fila
    assert (a["date"], a["ticker"], a["side"]) == ("2024-01-15", "SPY", "buy")
    assert (a["quantity"], a["price"], a["fee"]) == (10.0, 450.2, 1.5)
    assert a["name"] == "SPDR S&P 500"

    b = by_note['toma parcial "planificada"']        # las comillas sobrevivieron
    assert b["side"] == "sell" and b["quantity"] == 4.0

    c = [r for r in rows if r["ticker"] == "0P0000XYZ.F"][0]
    assert c["date"] == ""                            # sin fecha sigue sin fecha
    assert c["name"] == "Fondo Índice Acciones España"   # los acentos, intactos
    assert c["fee"] == 0.0


def test_export_keeps_full_quantity_precision():
    """Las participaciones de un fondo llevan seis o más decimales. Redondear al
    exportar no se ve en pantalla y falsea el precio medio al reimportar."""
    db = _fresh_db()
    qty = 3.14159265358979
    _insert(db, [("2024-03-01", "0P0001ABC.F", "F", "Fondo", "buy", qty, 12.3456789, 0.0, "")])

    rows, errors, _ = D._parse_upload("c.csv", _export_bytes(db).data)

    assert errors == []
    assert rows[0]["quantity"] == qty          # idéntico, no "parecido"
    assert rows[0]["price"] == 12.3456789


def test_export_omits_the_instrument_type_on_purpose():
    """`kind` lo decide el símbolo en cada lectura y escritura, justamente para
    que una etiqueta vieja no pueda re-etiquetar una posición. Exportarlo
    construiría el viaje de vuelta que esa regla existe para impedir."""
    db = _fresh_db()
    _insert(db, [("2024-01-01", "SPY", "SPDR", "ETF", "buy", 1.0, 100.0, 0.0, "")])

    header = _export_bytes(db).data.decode("utf-8-sig").splitlines()[0]

    assert header == "fecha,ticker,nombre,tipo,cantidad,precio,comision,nota"
    assert "kind" not in header and "tipo_activo" not in header


def test_export_says_compra_and_venta_not_c_and_v():
    """Un código de una letra está a una edición descuidada de invertir una
    operación; la palabra completa sobrevive a que alguien abra el CSV."""
    db = _fresh_db()
    _insert(db, [("2024-01-01", "AAA", "", "", "buy", 1.0, 10.0, 0.0, ""),
                 ("2024-01-02", "AAA", "", "", "sell", 1.0, 11.0, 0.0, "")])

    body = _export_bytes(db).data.decode("utf-8-sig")

    assert ",compra," in body and ",venta," in body
    rows, _, _ = D._parse_upload("c.csv", body.encode("utf-8-sig"))
    assert [r["side"] for r in sorted(rows, key=lambda r: r["date"])] == ["buy", "sell"]


def test_export_is_an_attachment_and_is_never_cached():
    """Es la cartera entera en un GET: ni el navegador ni un proxy delante deben
    quedarse una copia en disco."""
    db = _fresh_db()
    _insert(db, [("2024-01-01", "AAA", "", "", "buy", 1.0, 10.0, 0.0, "")])

    r = _export_bytes(db)

    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Content-Disposition"].startswith("attachment; filename=")
    assert "text/csv" in r.headers["Content-Type"]


def test_export_of_an_empty_book_is_a_header_not_an_error():
    db = _fresh_db()
    r = _export_bytes(db)
    assert r.status_code == 200
    assert r.data.decode("utf-8-sig").strip() == "fecha,ticker,nombre,tipo,cantidad,precio,comision,nota"


# ── copia de seguridad ────────────────────────────────────────────────────
def test_backup_copies_a_live_database_coherently():
    db = _fresh_db()
    _insert(db, [("2024-01-01", "AAA", "", "", "buy", 1.0, 10.0, 0.0, "x")] * 5)
    dest = tempfile.mkdtemp()

    assert B.make_backup(db, dest, quiet=True) == B.EXIT_OK

    copies = B.existing(dest)
    assert len(copies) == 1
    ok, detail = B.verify(copies[0])
    assert ok and detail == "5 movimientos"


def test_backup_is_correct_with_wal_enabled():
    """Hoy la base usa el journal por defecto, pero activar WAL es una línea. En
    WAL los últimos commits viven en el `-wal` hasta el checkpoint, así que
    copiar sólo el `.db` pierde datos en silencio. `backup()` no."""
    db = _fresh_db()
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    with con:
        con.executemany(
            "INSERT INTO movements(date,ticker,side,quantity,price,fee) VALUES(?,?,?,?,?,0)",
            [("2024-01-01", "AAA", "buy", 1.0, 10.0)] * 7)
    dest = tempfile.mkdtemp()
    try:
        assert B.make_backup(db, dest, quiet=True) == B.EXIT_OK
    finally:
        con.close()

    ok, detail = B.verify(B.existing(dest)[0])
    assert ok and detail == "7 movimientos"


def test_backup_is_not_readable_by_anyone_else():
    db = _fresh_db()
    _insert(db, [("2024-01-01", "AAA", "", "", "buy", 1.0, 10.0, 0.0, "")])
    dest = os.path.join(tempfile.mkdtemp(), "backups")

    B.make_backup(db, dest, quiet=True)

    assert stat.S_IMODE(os.stat(dest).st_mode) & 0o077 == 0, "el directorio deja mirar"
    for path in B.existing(dest):
        assert stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0, path


def test_an_identical_backup_does_not_spend_the_retention():
    """Un libro que no se toca en tres meses no debe gastar catorce huecos en
    catorce copias iguales: lo que se conserva son estados DISTINTOS."""
    db = _fresh_db()
    _insert(db, [("2024-01-01", "AAA", "", "", "buy", 1.0, 10.0, 0.0, "")])
    dest = tempfile.mkdtemp()

    assert B.make_backup(db, dest, quiet=True) == B.EXIT_OK
    assert B.make_backup(db, dest, quiet=True) == B.EXIT_NOOP
    assert len(B.existing(dest)) == 1

    _insert(db, [("2024-02-01", "BBB", "", "", "buy", 2.0, 20.0, 0.0, "")])
    assert B.make_backup(db, dest, quiet=True) == B.EXIT_OK
    assert len(B.existing(dest)) == 2


def test_two_backups_in_the_same_second_do_not_overwrite_each_other():
    """La marca tiene resolución de segundo. Dos ejecuciones seguidas a mano
    pedían el mismo nombre, y el `os.replace` final se llevaba la copia
    anterior: una copia de seguridad borrando otra copia de seguridad."""
    db = _fresh_db()
    _insert(db, [("2024-01-01", "AAA", "", "", "buy", 1.0, 10.0, 0.0, "")])
    dest = tempfile.mkdtemp()

    assert B.make_backup(db, dest, quiet=True) == B.EXIT_OK
    _insert(db, [("2024-01-02", "BBB", "", "", "buy", 2.0, 20.0, 0.0, "")])
    assert B.make_backup(db, dest, quiet=True) == B.EXIT_OK      # mismo segundo

    copies = B.existing(dest)
    assert len(copies) == 2
    # y la PRIMERA de la lista es la nueva, no la vieja
    assert B.verify(copies[0])[1] == "2 movimientos"
    assert B.verify(copies[1])[1] == "1 movimientos"


def test_the_tiebreak_suffix_still_sorts_as_the_newest():
    """`-2` desempata, pero como texto ordena ANTES que `.db`. Si el orden se
    dejara a la comparación de cadenas, la rotación borraría la copia buena."""
    names = [f"{B.PREFIX}2024-01-01-120000{B.SUFFIX}",
             f"{B.PREFIX}2024-01-01-120000-2{B.SUFFIX}",
             f"{B.PREFIX}2024-01-01-120000-10{B.SUFFIX}"]
    assert sorted(names, key=B._sort_key) == names, "10 va después de 2, no antes"
    assert B._sort_key(names[0]) < B._sort_key(names[1])


def test_rotation_keeps_the_newest_and_deletes_the_rest():
    dest = tempfile.mkdtemp()
    for i in range(6):                       # copias de mentira, sólo el nombre importa
        open(os.path.join(dest, f"{B.PREFIX}2024-01-0{i}-000000{B.SUFFIX}"), "wb").close()
    db = _fresh_db()
    _insert(db, [("2024-01-01", "AAA", "", "", "buy", 1.0, 10.0, 0.0, "")])

    B.make_backup(db, dest, keep=3, quiet=True)

    names = [os.path.basename(p) for p in B.existing(dest)]
    assert len(names) == 3
    assert names == sorted(names, reverse=True), "se quedan las MÁS RECIENTES"


def test_no_database_yet_is_not_a_failure():
    """Una instalación recién hecha todavía no tiene cartera. El timer no debe
    marcarse como fallido por eso."""
    missing = os.path.join(tempfile.mkdtemp(), "no-existe.db")
    assert B.make_backup(missing, tempfile.mkdtemp(), quiet=True) == B.EXIT_NOOP


def test_verify_rejects_a_file_that_is_not_a_database():
    """La verificación es lo que separa una copia de una esperanza."""
    path = os.path.join(tempfile.mkdtemp(), "basura.db")
    with open(path, "wb") as f:
        f.write(b"esto no es SQLite, ni de lejos")
    ok, detail = B.verify(path)
    assert not ok and detail


def test_a_partial_copy_never_takes_the_good_name():
    """Si el proceso muere a mitad, lo que queda es un `.parcial` evidente y no
    una copia con el nombre correcto que nadie sabe que está truncada."""
    db = _fresh_db()
    _insert(db, [("2024-01-01", "AAA", "", "", "buy", 1.0, 10.0, 0.0, "")])
    dest = tempfile.mkdtemp()
    B.make_backup(db, dest, quiet=True)

    leftovers = [n for n in os.listdir(dest) if n.endswith(".parcial")]
    assert leftovers == []
    assert all(n.startswith(B.PREFIX) and n.endswith(B.SUFFIX) for n in os.listdir(dest))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
