#!/usr/bin/env python3
"""Copia de seguridad de `cartera.db` — el único estado irrecuperable del panel.

Todo lo demás se vuelve a calcular solo: las puntuaciones salen del motor, los
precios y el histórico salen de Yahoo, los pesos por país los reconstruye el
timer semanal. El libro de movimientos no sale de ningún sitio. Si se pierde,
se perdió.

POR QUÉ NO ES UN `cp`
---------------------
Porque la base está VIVA. Un `cp` mientras el panel escribe puede llevarse un
fichero a medio commit: abre sin quejarse y le faltan filas, que es la peor
forma de fallar que tiene una copia de seguridad. `Connection.backup()` es la
API de copia en línea de SQLite: coordina con el escritor y produce una
instantánea coherente sin bloquear al panel.

Y porque el modo de journal no es asunto de este script. Hoy la base usa el
rollback journal por defecto; si alguien activa WAL mañana, el `.db` por sí
solo deja de contener los últimos commits (viven en el `-wal` hasta el
siguiente checkpoint) y cualquier copia basada en ficheros se vuelve incorrecta
en silencio. `backup()` es correcta en los dos modos, así que esa decisión
puede cambiar sin que nadie tenga que acordarse de venir aquí.

QUÉ HACE
--------
  1. Copia coherente a `backups/cartera-<fecha>-<hora>.db`, permisos 0600.
  2. VERIFICA la copia recién hecha: `integrity_check` + cuenta de movimientos.
     Una copia que nadie ha abierto nunca es una esperanza, no una copia.
  3. Si es idéntica a la más reciente, la descarta. Un libro que no se toca en
     tres meses no debe gastar la retención en noventa copias iguales: así las
     que se guardan son estados DISTINTOS.
  4. Rota: conserva las `--keep` más recientes.

Uso:
    python3 backup_cartera.py                 # copia + rotación
    python3 backup_cartera.py --list          # qué copias hay
    python3 backup_cartera.py --verify FICHERO
    python3 backup_cartera.py --restore FICHERO   # imprime el cómo, no lo hace

Códigos de salida:  0 copia nueva · 2 sin cambios o sin base · 1 fallo real.
El 2 es un no-evento, y la unidad de systemd lo trata como éxito.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# El mismo origen que usa el panel, para que ambos miren siempre al mismo sitio
# cuando una instancia mueve su libro con la variable de entorno.
DEFAULT_DB = os.environ.get("CARTERA_DB") or os.path.join(HERE, "cartera.db")
DEFAULT_DIR = os.environ.get("CARTERA_BACKUP_DIR") or os.path.join(HERE, "backups")
DEFAULT_KEEP = 14
PREFIX, SUFFIX = "cartera-", ".db"

EXIT_OK, EXIT_FAIL, EXIT_NOOP = 0, 1, 2


def _log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, flush=True)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


STAMP_LEN = len("YYYY-MM-DD-HHMMSS")


def _sort_key(name: str) -> tuple[str, int]:
    """`(marca, desempate)` de un nombre de copia.

    NO vale comparar la cadena entera: `cartera-<marca>-2.db` es POSTERIOR a
    `cartera-<marca>.db`, pero el guion (0x2D) va antes que el punto (0x2E), así
    que una comparación de textos la coloca antes. Con eso, la más reciente
    dejaría de ser la primera y la rotación borraría la copia equivocada.
    """
    core = name[len(PREFIX):-len(SUFFIX)]
    stamp, extra = core[:STAMP_LEN], core[STAMP_LEN:].lstrip("-")
    try:
        return stamp, int(extra) if extra else 1
    except ValueError:
        return stamp, 1


def existing(dest_dir: str) -> list[str]:
    """Copias ya guardadas, de la más reciente a la más antigua.

    Ordenadas por NOMBRE, no por mtime: el nombre lleva la marca de tiempo de
    cuando se tomó la copia, y una restauración o un `rsync` reescriben el mtime
    sin que el contenido haya cambiado de fecha.
    """
    if not os.path.isdir(dest_dir):
        return []
    names = [n for n in os.listdir(dest_dir)
             if n.startswith(PREFIX) and n.endswith(SUFFIX)]
    return [os.path.join(dest_dir, n)
            for n in sorted(names, key=_sort_key, reverse=True)]


def _free_name(dest_dir: str, stamp: str) -> str:
    """Ruta libre para la marca `stamp`, desempatando con un sufijo si hace falta.

    La marca tiene resolución de segundo, así que dos ejecuciones dentro del
    mismo segundo pedirían el mismo nombre y el `os.replace` final se llevaría
    por delante la copia anterior. Con el timer diario no pasa nunca; a mano,
    sí. Y una copia de seguridad que borra otra copia de seguridad es
    exactamente el fallo que este fichero existe para no tener.
    """
    base = os.path.join(dest_dir, f"{PREFIX}{stamp}")
    if not os.path.exists(base + SUFFIX):
        return base + SUFFIX
    n = 2
    while os.path.exists(f"{base}-{n}{SUFFIX}"):
        n += 1
    return f"{base}-{n}{SUFFIX}"


def verify(path: str) -> tuple[bool, str]:
    """Abre la copia y comprueba que sirve. Devuelve `(ok, detalle)`."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                return False, f"integrity_check dice: {ok}"
            n = con.execute("SELECT COUNT(*) FROM movements").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as e:
        return False, f"no se puede leer: {e}"
    return True, f"{n} movimientos"


def make_backup(db: str, dest_dir: str, keep: int = DEFAULT_KEEP,
                quiet: bool = False) -> int:
    if not os.path.exists(db):
        _log(f"No hay base todavía en {db}: nada que copiar.", quiet)
        return EXIT_NOOP

    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    # `makedirs` respeta el umask, así que un directorio que ya existiera con
    # permisos anchos seguiría abierto. Se corrige en cada ejecución, igual que
    # el panel hace con el fichero de la base.
    try:
        if os.stat(dest_dir).st_mode & 0o077:
            os.chmod(dest_dir, 0o700)
    except OSError:
        pass

    final = _free_name(dest_dir, time.strftime("%Y-%m-%d-%H%M%S"))
    # Se escribe con nombre temporal y se renombra al final: si el proceso muere
    # a mitad, lo que queda es un `.parcial` evidente y no una copia con el
    # nombre correcto que nadie sabe que está truncada.
    tmp = final + ".parcial"

    try:
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(tmp)
            try:
                # 0600 ANTES de que entre un solo byte: en una máquina
                # compartida, crear el fichero con el umask por defecto y
                # ajustarlo después deja una ventana en la que la cartera
                # entera es legible por cualquier cuenta local.
                os.chmod(tmp, 0o600)
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
    except (sqlite3.Error, OSError) as e:
        _log(f"FALLO al copiar: {e}", quiet)
        if os.path.exists(tmp):
            os.unlink(tmp)
        return EXIT_FAIL

    ok, detail = verify(tmp)
    if not ok:
        _log(f"FALLO: la copia no verifica ({detail}). Se descarta.", quiet)
        os.unlink(tmp)
        return EXIT_FAIL

    # Idéntica a la anterior -> no gasta retención. Se compara con la copia más
    # reciente, no con todas: lo que interesa es "¿ha cambiado el libro desde la
    # última vez?", y un estado que vuelve a uno antiguo SÍ es un cambio.
    prev = existing(dest_dir)
    if prev and _sha256(tmp) == _sha256(prev[0]):
        os.unlink(tmp)
        _log(f"Sin cambios desde {os.path.basename(prev[0])} ({detail}).", quiet)
        return EXIT_NOOP

    os.replace(tmp, final)
    _log(f"Copia: {final}  ({detail})", quiet)

    for old in existing(dest_dir)[keep:]:
        try:
            os.unlink(old)
            _log(f"  rotada: {os.path.basename(old)}", quiet)
        except OSError as e:
            _log(f"  no se pudo rotar {os.path.basename(old)}: {e}", quiet)
    return EXIT_OK


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default=DEFAULT_DB, help="base a copiar")
    p.add_argument("--dir", default=DEFAULT_DIR, dest="dest",
                   help="dónde guardar las copias")
    p.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                   help=f"copias distintas a conservar (por defecto {DEFAULT_KEEP})")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--list", action="store_true", help="listar copias y salir")
    p.add_argument("--verify", metavar="FICHERO", help="verificar una copia y salir")
    p.add_argument("--restore", metavar="FICHERO",
                   help="imprimir los pasos de restauración (no toca nada)")
    a = p.parse_args(argv)

    if a.verify:
        ok, detail = verify(a.verify)
        print(f"{'OK' if ok else 'FALLO'}: {a.verify} — {detail}")
        return EXIT_OK if ok else EXIT_FAIL

    if a.list:
        rows = existing(a.dest)
        if not rows:
            print(f"No hay copias en {a.dest}")
            return EXIT_NOOP
        for path in rows:
            ok, detail = verify(path)
            size = os.path.getsize(path) / 1024
            print(f"{'ok  ' if ok else 'MAL '} {os.path.basename(path):<34} "
                  f"{size:>8.1f} KB  {detail}")
        return EXIT_OK

    if a.restore:
        # Restaurar se explica, no se automatiza: el paso que borra el libro en
        # uso lo tiene que teclear una persona que sabe lo que está haciendo.
        print(f"""Para restaurar {a.restore}:

  1. Parar el panel para que nadie escriba a mitad:
       systemctl --user stop market-zones

  2. Apartar el libro actual (no lo borres — igual la copia no era la que creías):
       mv {a.db} {a.db}.antes-de-restaurar
       rm -f {a.db}-wal {a.db}-shm

  3. Poner la copia en su sitio:
       cp {a.restore} {a.db}
       chmod 600 {a.db}

  4. Arrancar y comprobar el número de movimientos en pantalla:
       systemctl --user start market-zones

Los ficheros -wal y -shm del paso 2 se borran a propósito: pertenecen a la base
que estás sustituyendo, y dejarlos junto a otra base es cómo se corrompe una
restauración que iba bien.""")
        return EXIT_OK

    return make_backup(a.db, a.dest, a.keep, a.quiet)


if __name__ == "__main__":
    sys.exit(main())
