#!/usr/bin/env python3
"""Aviso cuando una posición de la cartera ENTRA o SALE de una zona extrema.

POR QUÉ ESTO NO ES «TIMING»
---------------------------
Este panel no predice, y está medido: el buy&hold gana a cualquier regla de
entrada y salida que se le ha probado, y `GUIA.md` lo dice sin adornos. Así que
esto no avisa de que haya que comprar ni vender NADA.

Avisa de un cambio de ESTADO: un activo que ya está en la cartera acaba de
entrar en Capitulación o en Euforia. Eso es un hecho sobre el precio de hoy
frente a su propia historia, no un pronóstico sobre mañana, y es exactamente el
tipo de cosa que no se entera uno mirando la pantalla —- porque no se está
mirando la pantalla el día que pasa.

QUÉ HACE
--------
  1. Lee las posiciones ABIERTAS del libro de movimientos.
  2. Calcula la zona de hoy de cada una con el MISMO motor que el panel.
  3. La compara con la que vio la última vez.
  4. Avisa sólo de las transiciones que tocan un extremo.

La primera ejecución NO avisa de nada: siembra el estado. Si no, el día que se
instala esto suelta un aviso por cada posición que ya estuviera en un extremo
desde hace meses, y un aviso que no corresponde a un cambio enseña a ignorar
los avisos.

Uso:
    python3 alertas_cartera.py             # comprobar y avisar
    python3 alertas_cartera.py --list      # zona actual de cada posición
    python3 alertas_cartera.py --dry-run   # qué avisaría, sin mandar ni guardar
    python3 alertas_cartera.py --all       # cualquier cambio de zona, no sólo extremos

Telegram es OPCIONAL: si no hay `MZ_TELEGRAM_TOKEN` y `MZ_TELEGRAM_CHAT_ID`, el
aviso sale por la salida estándar y queda en el journal. Sin claves inventadas y
sin fallar por no tenerlas.

Códigos de salida:  0 hubo aviso · 2 nada que decir · 1 fallo real.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_DB = os.environ.get("CARTERA_DB") or os.path.join(HERE, "cartera.db")
STATE = os.environ.get("CARTERA_ALERTS_STATE") or os.path.join(HERE, "alertas_cartera.json")
YEARS = 25

# Las dos puntas de la escala. Son las únicas que describen un estado poco
# frecuente: Equilibrio es donde vive un activo la mayor parte del tiempo, y
# avisar de que algo «ha entrado en Equilibrio» es avisar de nada.
EXTREMES = ("Capitulación", "Euforia")

EXIT_OK, EXIT_FAIL, EXIT_NOOP = 0, 1, 2


def open_positions(db: str) -> list[str]:
    """Tickers con cantidad neta positiva. Los dividendos no dan ni quitan
    títulos, así que no cuentan aquí."""
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT ticker, side, quantity FROM movements").fetchall()
    finally:
        con.close()
    net: dict[str, float] = {}
    for tk, side, q in rows:
        if not tk or side == "div":
            continue
        net[tk] = net.get(tk, 0.0) + (q or 0.0) * (1 if side == "buy" else -1)
    return sorted(t for t, q in net.items() if q > 1e-9)


def zone_of(symbol: str) -> dict:
    """Zona de hoy, con el modelo diario y el peso por defecto — la lectura
    canónica, la misma que enseña la tabla de posiciones."""
    from zones import analyze, fetch_daily
    df = fetch_daily(symbol, years=YEARS)
    if symbol.upper().endswith("=F"):
        df = df.drop(columns=["volume"], errors="ignore")
    _frame, s = analyze(df)
    return {"zone": s.zone_name, "score": round(float(s.score), 2),
            "date": str(s.date.date())}


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    # Escritura atómica: un corte a mitad dejaría un JSON roto, y un estado
    # ilegible se lee como «primera ejecución», que vuelve a sembrar en silencio
    # y se come el aviso del día siguiente.
    tmp = path + ".parcial"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)      # dice qué tienes en cartera: no es público
    except OSError:
        pass


def transitions(prev: dict, now: dict, everything: bool = False) -> list[str]:
    """Los cambios que merecen un aviso, en texto ya listo para leer."""
    out = []
    for tk in sorted(now):
        new = now[tk]["zone"]
        old = (prev.get(tk) or {}).get("zone")
        if old is None or old == new:
            continue                     # posición nueva, o sin novedad
        if not everything and old not in EXTREMES and new not in EXTREMES:
            continue
        sc = now[tk]["score"]
        if new in EXTREMES:
            out.append(f"⚠️ {tk}: {old} → *{new}* (score {sc})")
        else:
            out.append(f"↩️ {tk}: sale de {old} → {new} (score {sc})")
    return out


def notify(lines: list[str], dry: bool = False) -> None:
    body = ("*Cartera · cambio de zona*\n" + "\n".join(lines)
            + "\n\n_Es un estado, no una recomendación: este panel no hace timing._")
    print(body, flush=True)
    token = os.environ.get("MZ_TELEGRAM_TOKEN")
    chat = os.environ.get("MZ_TELEGRAM_CHAT_ID")
    if dry or not token or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": body,
                                   "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
    except (urllib.error.URLError, OSError) as e:
        # Un Telegram caído no puede tumbar la comprobación: el aviso ya salió
        # por el journal, que es donde queda constancia.
        print(f"aviso: no pude entregar por Telegram ({e})", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--list", action="store_true", help="zona actual, sin avisar")
    ap.add_argument("--dry-run", action="store_true", help="qué avisaría, sin mandar ni guardar")
    ap.add_argument("--all", action="store_true", help="cualquier cambio, no sólo extremos")
    a = ap.parse_args(argv)

    tickers = open_positions(a.db)
    if not tickers:
        print("sin posiciones abiertas", flush=True)
        return EXIT_NOOP

    now, failed = {}, []
    for tk in tickers:
        try:
            now[tk] = zone_of(tk)
        except Exception as e:
            # Un instrumento que no se puede puntuar NO se apunta como cambio ni
            # borra lo que ya se sabía de él: se queda fuera y se dice.
            failed.append(f"{tk} ({type(e).__name__})")

    if a.list:
        for tk in sorted(now):
            z = now[tk]
            print(f"{tk:<16} {z['zone']:<14} score {z['score']:>6}  ({z['date']})")
        for f in failed:
            print(f"{f:<16} sin puntuar")
        return EXIT_OK

    prev = load_state(a.state)
    first = not prev
    lines = [] if first else transitions(prev, now, a.all)

    if not a.dry_run:
        # El estado conserva lo que ya se sabía de un ticker que hoy falló: si no,
        # un tropiezo de Yahoo lo dejaría sin historia y el aviso saldría mañana
        # como si fuera un cambio.
        merged = dict(prev)
        merged.update(now)
        save_state(a.state, merged)

    if first:
        print(f"primera ejecución: sembradas {len(now)} posiciones, sin avisos", flush=True)
        return EXIT_NOOP
    if failed:
        print("sin puntuar: " + ", ".join(failed), file=sys.stderr)
    if not lines:
        print(f"{len(now)} posiciones comprobadas, ningún cambio de zona", flush=True)
        return EXIT_NOOP
    notify(lines, a.dry_run)
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_FAIL)
