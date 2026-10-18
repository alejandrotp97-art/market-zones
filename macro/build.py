#!/usr/bin/env python3
"""Foto del ciclo económico: descarga todo, clasifica y lo guarda en un JSON.

Lo ejecuta el timer `market-zones-ciclo` una vez a la semana (domingo, 07:30
hora de Madrid), pero también sirve a mano:

    .venv/bin/python -m macro.build

El panel sólo LEE la foto (`data/macro/ciclo.json`); la recalcula él mismo si no
existe o tiene más de 8 días. Una serie que no se pudo bajar no tumba la foto: se
calcula con el resto y queda listada en `sources.failed`. Sin el CLI de la OCDE
(o sin uno reciente) no hay reloj, y entonces no hay foto.

Códigos de salida:  0 foto escrita · 1 no se pudo calcular.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import cycle as C
from . import news as N
from . import pillars as P
from . import sources as S

SNAPSHOT_PATH = os.path.join(S.DEFAULT_DIR, "ciclo.json")
HEADLINES_SHOWN = 8

SUBSTITUTES = [
    {"wanted": "The Conference Board — LEI", "used": "CLI de la OCDE (EE. UU. y G20)",
     "why": "el LEI es de pago"},
    {"wanted": "The Conference Board — CEI", "used": "sus componentes en FRED: empleo, producción "
     "industrial, renta real y ventas reales", "why": "el CEI es de pago"},
    {"wanted": "CME FedWatch", "used": "bono a 2 años − tipo efectivo de la Fed (FRED)",
     "why": "FedWatch no tiene acceso público"},
]


class BuildError(RuntimeError):
    pass


def collect(cache: S.Cache) -> tuple[dict, list[dict], list[dict]]:
    """(series, titulares, estado por fuente). Nunca lanza por una fuente suelta."""
    data: dict = {}
    status: list[dict] = []

    def one(sid):
        try:
            s, got = S.fred(sid, cache)
            return sid, s, {"id": sid, "ok": True, "stale": got.stale, "fetched_at": got.fetched_at}
        except S.SourceError as e:
            return sid, None, {"id": sid, "ok": False, "error": str(e)}

    # FRED aguanta bien unas pocas peticiones en paralelo; en serie se sumarían las 20 latencias.
    with ThreadPoolExecutor(6) as ex:
        for sid, s, st in ex.map(one, P.FRED_IDS):
            status.append(st)
            if s:
                data[sid] = s
    try:
        cli, got = S.oecd_cli(cache)
        for area, s in cli.items():
            data[f"CLI:{area}"] = s
        status.append({"id": "OECD-CLI", "ok": True, "stale": got.stale, "fetched_at": got.fetched_at})
    except S.SourceError as e:
        status.append({"id": "OECD-CLI", "ok": False, "error": str(e)})
    headlines: list[dict] = []
    try:
        headlines, got = N.fetch_headlines(S.Cache(cache.dir, ttl=3 * 3600, getter=cache.getter))
        status.append({"id": "GoogleNews", "ok": True, "stale": got.stale, "fetched_at": got.fetched_at})
    except (S.SourceError, N.NewsError) as e:
        status.append({"id": "GoogleNews", "ok": False, "error": str(e)})
    return data, headlines, status


def snapshot(data: dict, headlines: list[dict], status: list[dict],
             today: str | None = None, now: float | None = None) -> dict:
    if not data.get("CLI:USA"):
        raise BuildError("sin el CLI de la OCDE no hay reloj del ciclo")
    today = today or dt.date.today().isoformat()
    tone = N.tone(headlines)
    data = dict(data)
    if tone["score"] is not None:
        data["NEWS_TONE"] = [(today, tone["score"])]
    r = C.classify(data)
    if r["clock"] is None:
        # Presente pero inservible (viejo o sin historia para el impulso): una
        # foto sin fase dejaría la tarjeta en blanco.
        raise BuildError("el CLI de la OCDE no tiene un dato reciente con el que situar el reloj")
    shown = sorted(tone["headlines"], key=lambda h: h["mood"] == 0)   # estable: primero con tono
    r.update({
        "generated_at": time.time() if now is None else now,
        "phases": [{"key": k, "label": lab} for k, lab in C.PHASES],
        "news": {"score": tone["score"], "n": tone["n"], "neg": tone["neg"], "pos": tone["pos"],
                 "headlines": shown[:HEADLINES_SHOWN]},
        "sources": {"status": status, "failed": [s["id"] for s in status if not s["ok"]],
                    "stale": [s["id"] for s in status if s.get("stale")]},
        "substitutes": SUBSTITUTES,
    })
    return r


def write_snapshot(snap: dict, path: str | None = None) -> dict:
    path = path or SNAPSHOT_PATH
    S.atomic_write_text(path, json.dumps(snap, ensure_ascii=False))
    return snap


def read_snapshot(path: str | None = None) -> dict | None:
    try:
        with open(path or SNAPSHOT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def refresh(cache: S.Cache | None = None) -> dict:
    data, headlines, status = collect(cache or S.Cache())
    return write_snapshot(snapshot(data, headlines, status))


def main() -> int:
    try:
        snap = refresh()
    except BuildError as e:
        print(f"No pude calcular el ciclo: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"phase": snap["phase"], "confidence": round(snap["confidence"], 3),
                      "as_of": snap["as_of"], "failed": snap["sources"]["failed"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
