"""El aviso de cambio de zona: cuándo habla y, sobre todo, cuándo se calla.

Un avisador que habla de más enseña a ignorarlo, y a partir de ahí ya no avisa
de nada. Cada caso de aquí fija un silencio deliberado.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alertas_cartera as A


def _z(zone, score=50.0):
    return {"zone": zone, "score": score, "date": "2026-08-18"}


# ── qué merece un aviso ───────────────────────────────────────────────────
def test_avisa_al_entrar_en_un_extremo():
    lineas = A.transitions({"AAA": _z("Acumulación")}, {"AAA": _z("Capitulación", 12)})
    assert len(lineas) == 1
    assert "Capitulación" in lineas[0] and "AAA" in lineas[0]


def test_avisa_tambien_al_salir_de_un_extremo():
    """Salir es tan informativo como entrar: el estado por el que alguien estaba
    pendiente se ha terminado."""
    lineas = A.transitions({"AAA": _z("Euforia")}, {"AAA": _z("Precaución", 70)})
    assert len(lineas) == 1
    assert "sale de" in lineas[0]


def test_callado_cuando_la_zona_no_cambia():
    assert A.transitions({"AAA": _z("Euforia")}, {"AAA": _z("Euforia", 88)}) == []


def test_callado_en_los_vaivenes_del_centro():
    """Equilibrio <-> Precaución es donde vive un activo la mayor parte del
    tiempo. Avisar de eso es avisar todos los días, que es no avisar."""
    assert A.transitions({"AAA": _z("Equilibrio")}, {"AAA": _z("Precaución")}) == []


def test_con_all_si_avisa_de_cualquier_cambio():
    lineas = A.transitions({"AAA": _z("Equilibrio")}, {"AAA": _z("Precaución")},
                           everything=True)
    assert len(lineas) == 1


def test_una_posicion_nueva_no_es_un_cambio():
    """Comprar algo que ya estaba en Capitulación no es una novedad sobre el
    activo: es una decisión que acaba de tomar quien lo compró."""
    assert A.transitions({}, {"AAA": _z("Capitulación")}) == []


# ── el estado ─────────────────────────────────────────────────────────────
def test_la_primera_ejecucion_siembra_y_no_avisa(tmp_path, monkeypatch):
    """El día de la instalación soltaría un aviso por cada posición que ya
    estuviera en un extremo desde hace meses. Un aviso que no corresponde a un
    cambio es exactamente lo que entrena a nadie a mirarlos."""
    db = str(tmp_path / "cartera.db")
    _libro(db, [("AAA", "buy", 10)])
    estado = str(tmp_path / "estado.json")
    monkeypatch.setattr(A, "zone_of", lambda s: _z("Capitulación", 8))

    rc = A.main(["--db", db, "--state", estado])

    assert rc == A.EXIT_NOOP
    assert json.load(open(estado))["AAA"]["zone"] == "Capitulación"


def test_el_estado_conserva_lo_que_ya_sabia_de_un_ticker_que_hoy_fallo(tmp_path, monkeypatch):
    """Si un tropiezo de Yahoo lo borrase, mañana volvería a entrar como
    posición nueva y el aviso del cambio se perdería para siempre."""
    db = str(tmp_path / "cartera.db")
    _libro(db, [("AAA", "buy", 10), ("BBB", "buy", 5)])
    estado = str(tmp_path / "estado.json")
    A.save_state(estado, {"AAA": _z("Equilibrio"), "BBB": _z("Euforia")})

    def falla_bbb(sym):
        if sym == "BBB":
            raise OSError("yahoo se cayó")
        return _z("Capitulación", 9)
    monkeypatch.setattr(A, "zone_of", falla_bbb)

    A.main(["--db", db, "--state", estado])

    guardado = json.load(open(estado))
    assert guardado["AAA"]["zone"] == "Capitulación"
    assert guardado["BBB"]["zone"] == "Euforia"        # intacto, no borrado


def test_dry_run_no_toca_el_estado(tmp_path, monkeypatch):
    db = str(tmp_path / "cartera.db")
    _libro(db, [("AAA", "buy", 10)])
    estado = str(tmp_path / "estado.json")
    A.save_state(estado, {"AAA": _z("Equilibrio")})
    monkeypatch.setattr(A, "zone_of", lambda s: _z("Capitulación", 9))

    A.main(["--db", db, "--state", estado, "--dry-run"])

    assert json.load(open(estado))["AAA"]["zone"] == "Equilibrio"


def test_un_estado_ilegible_no_se_traga_el_aviso_del_dia_siguiente(tmp_path):
    """Un JSON roto se lee como primera ejecución, así que la escritura es
    atómica. Aquí se comprueba que un fichero corrupto no revienta el script."""
    p = str(tmp_path / "roto.json")
    open(p, "w").write("{esto no es json")
    assert A.load_state(p) == {}


# ── qué posiciones se miran ───────────────────────────────────────────────
def _libro(db, filas):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE movements(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "date TEXT, ticker TEXT, side TEXT, quantity REAL, price REAL,"
                "fee REAL, note TEXT)")
    con.executemany("INSERT INTO movements(date,ticker,side,quantity,price,fee,note) "
                    "VALUES('2024-01-01',?,?,?,1,0,'')", filas)
    con.commit(); con.close()


def test_solo_las_posiciones_abiertas(tmp_path):
    db = str(tmp_path / "c.db")
    _libro(db, [("AAA", "buy", 10), ("BBB", "buy", 5), ("BBB", "sell", 5)])
    assert A.open_positions(db) == ["AAA"]


def test_un_dividendo_no_abre_ni_cierra_una_posicion(tmp_path):
    db = str(tmp_path / "c.db")
    _libro(db, [("AAA", "buy", 10), ("AAA", "sell", 10), ("AAA", "div", 3)])
    assert A.open_positions(db) == []


def test_sin_libro_no_hay_nada_que_mirar(tmp_path):
    assert A.open_positions(str(tmp_path / "no-existe.db")) == []
