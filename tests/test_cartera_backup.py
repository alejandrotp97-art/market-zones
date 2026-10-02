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

import pytest

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


def test_una_nota_que_excel_leeria_como_FORMULA_sale_neutralizada():
    """Una nota que llega del extracto del banco empezando por = o @ es una
    FÓRMULA para Excel y Sheets: se ejecuta al abrir el fichero. Sale con un
    apóstrofo delante, que es el escape que esos programas entienden, y el
    importador se lo quita, así que el ida y vuelta cae en la MISMA cadena.
    """
    db = _fresh_db()
    peligrosas = [
        "=1+1",                                   # fórmula a secas
        "@SUM(A1:A9)",                            # otra forma de arrancarla
        "+34 600 111 222",                        # un teléfono, no una fórmula
        "-5% de comisión",                        # un descuento, tampoco
        "'ya empezaba por apóstrofo",             # el escape no puede comérselo
        "compra normal",                          # y lo inocuo no se toca
    ]
    _insert(db, [(f"2024-01-1{i}", "SPY", "SPDR S&P 500", "ETF", "buy",
                  1.0, 100.0, 0.0, nota)
                 for i, nota in enumerate(peligrosas, start=1)])

    raw = _export_bytes(db).data
    texto = raw.decode("utf-8-sig")

    # En el fichero, ninguna celda arranca la fórmula.
    for linea in texto.splitlines()[1:]:
        for celda in linea.split(","):
            assert not celda.lstrip('"').startswith(("=", "@")), linea

    # Y al volver a entrar, la nota es exactamente la que era.
    rows, errors, _ = D._parse_upload("cartera.csv", raw)
    assert errors == [], errors
    assert sorted(r["note"] for r in rows) == sorted(peligrosas)


def test_el_desescapado_es_SOLO_para_nuestro_export():
    """El apóstrofo inicial lo puso este programa, y sólo en SU fichero. En un
    extracto del banco es del banco, y quitarlo borra un carácter de verdad. Y
    va ANTES del `.strip()`: al revés, una nota que sea sólo un tabulador
    vuelve convertida en un apóstrofo, que no es lo que había ni lo que había
    antes del cambio."""
    ajeno = (b"fecha,ticker,observaciones,cantidad,precio\n"
             b"2024-01-15,SPY,'-5% custodia,1,100\n")
    rows, errores, _ = D._parse_upload("extracto_banco.csv", ajeno)
    assert errores == [] and rows[0]["note"] == "'-5% custodia"

    db = _fresh_db()
    _insert(db, [("2024-01-15", "SPY", "SPDR", "ETF", "buy", 1.0, 100.0, 0.0, "\t")])
    vuelta, errores, _ = D._parse_upload("cartera.csv", _export_bytes(db).data)
    assert errores == [] and vuelta[0]["note"] == ""      # igual que antes del cambio


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


# ── la carpeta de copias es de UN solo libro ──────────────────────────────
def test_la_carpeta_de_copias_de_la_instancia_no_admite_otro_libro():
    """El fallo real: una copia de un libro de TEST entró en la carpeta de
    copias de producción y la rotación diaria fue expulsando, una por día, las
    14 copias de la cartera de verdad.

    No basta con derivar el destino en cada llamada: quien llegue mañana con el
    destino puesto a mano —otro script, un refactor, una prueba— tiene que
    chocar contra el que escribe, no contra la buena voluntad de quien lo llamó.
    """
    db = _fresh_db()                      # un libro que NO es el de la instancia
    antes = len(B.existing(B.DEFAULT_DIR))

    with pytest.raises(B.BackupDeOtroLibro):
        B.make_backup(db, B.DEFAULT_DIR, quiet=True)

    assert len(B.existing(B.DEFAULT_DIR)) == antes, "ha escrito en la carpeta ajena"


def test_un_libro_ajeno_se_puede_copiar_en_su_propia_carpeta():
    """La regla protege la carpeta, no la operación: copiar un libro de test es
    legítimo si la copia va donde vive ese libro."""
    db = _fresh_db()
    dest = os.path.join(tempfile.mkdtemp(), "backups")

    assert B.make_backup(db, dest, quiet=True) == B.EXIT_OK
    assert len(B.existing(dest)) == 1


def test_por_defecto_el_cli_copia_junto_a_la_base_que_le_indican():
    """`backup_cartera.py --db /otra/cartera.db` sin `--dir` apuntaba a la
    carpeta de copias de ESTA instancia: otro origen, mismo cubo. Por defecto el
    destino lo da la base que se va a copiar."""
    db = _fresh_db()
    antes = len(B.existing(B.DEFAULT_DIR))

    assert B.main(["--db", db, "--quiet"]) == B.EXIT_OK

    assert len(B.existing(B.DEFAULT_DIR)) == antes, "ha tocado la carpeta ajena"
    assert len(B.existing(os.path.join(os.path.dirname(db), "backups"))) == 1


def test_dos_libros_en_la_misma_carpeta_no_comparten_cubo(tmp_path, monkeypatch):
    """`nacho.db` y `nico.db` viven en la MISMA carpeta, así que derivar el
    destino «al lado de la base» les daría el mismo cubo: una rotación para dos
    libros distintos, listados mezclados y ningún modo de saber qué copia es de
    quién. `CARTERA_BACKUP_DIR` es la salida — una variable por instancia."""
    db = str(tmp_path / "nacho.db")

    monkeypatch.delenv("CARTERA_BACKUP_DIR", raising=False)
    assert B._dir_for(db) == str(tmp_path / "backups")

    monkeypatch.setenv("CARTERA_BACKUP_DIR", str(tmp_path / "copia-nacho"))
    assert B._dir_for(db) == str(tmp_path / "copia-nacho")
