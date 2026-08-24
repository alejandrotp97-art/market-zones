#!/usr/bin/env python3
"""Construye el catálogo de fondos y ETFs europeos desde el registro de ESMA.

FIRDS publica cada semana todo lo admitido a negociación en la Unión. El fichero
de vehículos de inversión colectiva (`FULINS_C`) pesa **3,7 MB** comprimidos y
trae 18.000 ISIN con 150.000 líneas de cotización. Eso es el catálogo entero.

Por qué existe este script y no una llamada desde el panel:

  * **Buscar no puede depender de la red.** El buscador de Yahoo tiene un tope
    duro de SIETE resultados —comprobado pidiendo 10, 25 y 50— y no entiende
    lenguaje natural. Con el catálogo en local la búsqueda es SQL y no espera.
  * **La ingesta es lenta y la búsqueda no puede serlo.** Descargar y doblar
    150.000 registros lleva segundos; teclear en una casilla, no.

Qué NO hace, a propósito:

  * **No inventa el TER.** No está en el registro. Sigue declarándose a mano, y
    el panel de avisos ya dice cuántas posiciones lo tienen sin declarar.
  * **No etiqueta nada como UCITS.** FIRDS no publica el régimen regulatorio, y
    la palabra sólo aparece en el nombre del 27,9% de los ETF: filtrar por ella
    escondería dos tercios del universo real. Se publica el DOMICILIO, que sale
    del ISIN y es un hecho.

Uso:
    ./ingest_firds.py                 # descarga lo último y reconstruye
    ./ingest_firds.py --dry-run       # dice qué haría, sin escribir
    ./ingest_firds.py --file X.zip    # desde un fichero ya descargado
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cartera.firds import decode_cfi, etc_candidatos, fold, iter_listings

CATALOGO_DB = os.environ.get("MZ_CATALOGO_DB") or os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "market-zones", "instruments.db")

# El índice Solr de ESMA: dice qué ficheros hay publicados y dónde bajarlos.
SOLR = ("https://registers.esma.europa.eu/solr/esma_registers_firds_files/select"
        "?q=*&fq=file_type:FULINS&wt=json&rows={rows}&sort=publication_date+desc")
UA = {"User-Agent": "market-zones/1.0 (catálogo de instrumentos; uso personal)"}


def _get(url: str, timeout: int = 30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)


def ultimo_fichero(letra: str = "C") -> dict:
    """El `FULINS_<letra>` más reciente publicado.

    Se pregunta al índice en vez de construir el nombre a mano: el fichero lleva
    la fecha dentro y viene partido en 1..N trozos según cuánto ocupe esa semana.
    Adivinar el nombre funcionaría hasta la primera semana en que ESMA parta el
    fichero en dos, y fallaría en silencio.
    """
    with _get(SOLR.format(rows=60)) as r:
        docs = json.load(r)["response"]["docs"]
    de_esa_letra = [d for d in docs if d["file_name"].startswith(f"FULINS_{letra}_")]
    if not de_esa_letra:
        raise RuntimeError(f"el índice de ESMA no lista ningún FULINS_{letra}")
    ultima = max(d["publication_date"] for d in de_esa_letra)
    trozos = sorted((d for d in de_esa_letra if d["publication_date"] == ultima),
                    key=lambda d: d["file_name"])
    return {"published": ultima[:10], "parts": trozos}


# ── Escritura del catálogo ─────────────────────────────────────────────────
ESQUEMA = """
-- `ccy` cuelga del FONDO porque es la divisa en que está DENOMINADO, y es una
-- sola: cero de los 18.376 ISIN del fichero tienen más de una entre sus plazas.
-- La divisa en la que se CRUZA en cada mercado no está en el registro; se sabe
-- al pedir el precio, y hasta entonces no se afirma.
-- `n_listings` va desnormalizado: ordena la búsqueda, y contarlo por consulta
-- costaría un escaneo de 150.000 filas por cada tecla que se pulse.
CREATE TABLE funds(
    isin TEXT PRIMARY KEY, name TEXT, cfi TEXT, kind TEXT, dist_policy TEXT,
    domicile TEXT, ccy TEXT, issuer_lei TEXT, n_listings INTEGER,
    leveraged INTEGER DEFAULT 0);
CREATE INDEX funds_kind ON funds(kind, dist_policy);
CREATE TABLE listings(
    isin TEXT, mic TEXT, first_trade TEXT,
    PRIMARY KEY(isin, mic)) WITHOUT ROWID;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
-- FTS5 sobre TODAS las variantes del nombre, no sólo la que se enseña: quien
-- escriba el nombre abreviado que trae su bróker («iShsIII-Core MSCI World»)
-- tiene que encontrar lo mismo que quien escriba el comercial.
CREATE VIRTUAL TABLE funds_fts USING fts5(
    isin UNINDEXED, texto, tokenize='unicode61 remove_diacritics 2');
"""


def escribir(destino: str, fondos: dict, lineas: list, procedencia: dict) -> None:
    con = sqlite3.connect(destino)
    try:
        con.executescript(ESQUEMA)
        con.executemany(
            "INSERT INTO funds(isin,name,cfi,kind,dist_policy,domicile,ccy,"
            "issuer_lei,n_listings,leveraged) VALUES(:isin,:name,:cfi,:kind,"
            ":dist_policy,:domicile,:ccy,:issuer_lei,:n_listings,:leveraged)",
            fondos.values())
        # `INSERT OR IGNORE`: la misma (isin, mic) aparece repetida cuando un
        # mercado publica varios segmentos bajo el mismo código. Es la misma
        # línea vista dos veces, no dos sitios donde comprar.
        con.executemany(
            "INSERT OR IGNORE INTO listings(isin,mic,first_trade)"
            " VALUES(:isin,:mic,:first_trade)", lineas)
        con.executemany(
            "INSERT INTO funds_fts(isin,texto) VALUES(?,?)",
            ((f["isin"], " ".join([f["isin"], *f["aliases"]])) for f in fondos.values()))
        con.executemany("INSERT INTO meta(key,value) VALUES(?,?)",
                        [(k, str(v)) for k, v in procedencia.items()])
        con.commit()
    finally:
        con.close()


def ingerir(zips: list, procedencia: dict, destino: str) -> dict:
    """Parsea, dobla y **reemplaza el catálogo de golpe**.

    Se escribe en un fichero temporal y se renombra encima al final. Un
    `os.replace` es atómico dentro del mismo sistema de ficheros: o el panel lee
    el catálogo viejo entero, o el nuevo entero. Nunca uno a medio construir,
    que es lo que pasaría vaciando las tablas e insertando en vivo.
    """
    def todas():
        for z in zips:
            with zipfile.ZipFile(z) as zf:
                for nombre in zf.namelist():
                    if nombre.lower().endswith(".xml"):
                        with zf.open(nombre) as fh:
                            yield from iter_listings(fh)

    t0 = time.time()
    # DOS pasadas, y la primera existe por memoria. El fichero de deuda trae
    # 642.598 ISIN de los que sólo unos 900 son ETC; doblarlos todos para tirar
    # el 99,86% picaba en 1,47 GB, por encima del tope de la unidad de systemd.
    # La primera pasada se queda con un conjunto de cadenas y nada más.
    candidatos = etc_candidatos(todas())
    fondos, lineas = fold(
        todas(),
        keep=lambda r: decode_cfi(r["cfi"])["is_civ"] or r["isin"] in candidatos)

    # `fold` clasifica; aquí se descarta lo que no llegó a ser un producto de
    # inversión. Se CUENTAN los descartes en vez de callarlos: si un día ESMA
    # añade un grupo CFI que no conocemos, el número se mueve y se ve.
    fuera = [k for k, f in fondos.items() if f["kind"] is None]
    for k in fuera:
        del fondos[k]
    lineas = [x for x in lineas if x["isin"] in fondos]

    procedencia = {**procedencia, "funds": len(fondos), "listings": len(lineas),
                   "unclassified": len(fuera),
                   "etc": sum(1 for f in fondos.values() if f["kind"] == "ETC"),
                   "ingested": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        prefix=".instruments-", suffix=".db",
        dir=os.path.dirname(os.path.abspath(destino)), delete=False)
    tmp.close()
    os.unlink(tmp.name)
    try:
        escribir(tmp.name, fondos, lineas, procedencia)
        os.chmod(tmp.name, 0o644)          # datos públicos: no hay nada que tapar
        os.replace(tmp.name, destino)
    except BaseException:
        for resto in (tmp.name, tmp.name + "-wal", tmp.name + "-shm"):
            try:
                os.unlink(resto)
            except OSError:
                pass
        raise
    return {**procedencia, "seconds": round(time.time() - t0, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="dice qué fichero usaría y no escribe nada")
    ap.add_argument("--file", action="append", default=[],
                    help="usa un .zip ya descargado en vez de bajarlo (repetible)")
    ap.add_argument("--db", default=CATALOGO_DB)
    ap.add_argument("--letter", default="CD",
                    help="qué FULINS traer. C = fondos y ETFs; D = deuda, de donde"
                         " salen los ETC. Por defecto los dos.")
    a = ap.parse_args(argv)

    if a.file:
        procedencia = {"source": "fichero local", "files": ", ".join(map(os.path.basename, a.file)),
                       "published": "?"}
        zips = a.file
        tmpdir = None
    else:
        # Dos ficheros y no uno: `_C` son los fondos y ETFs, `_D` es deuda, y los
        # ETC —oro físico, materias primas— son jurídicamente deuda. Sin `_D` el
        # buscador no encuentra el oro, que es de lo primero que se busca.
        partes, nombres = [], []
        for letra in a.letter.upper():
            info = ultimo_fichero(letra)
            partes += info["parts"]
            nombres += [x["file_name"] for x in info["parts"]]
            print(f"último FULINS_{letra}: {info['published']} · "
                  f"{len(info['parts'])} fichero(s)")
        publicado = max(x["publication_date"] for x in partes)[:10]
        if a.dry_run:
            print("ficheros: " + ", ".join(nombres))
            print("--dry-run: no descargo ni escribo")
            return 0
        tmpdir = tempfile.mkdtemp(prefix="firds-")
        zips = []
        for p in partes:
            ruta = os.path.join(tmpdir, p["file_name"])
            print(f"  bajando {p['file_name']}…", flush=True)
            with _get(p["download_link"], timeout=300) as r, open(ruta, "wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
            zips.append(ruta)
        procedencia = {"source": "ESMA FIRDS", "files": ", ".join(nombres),
                       "published": publicado}

    if a.dry_run:
        print("--dry-run: no escribo")
        return 0
    try:
        r = ingerir(zips, procedencia, a.db)
    finally:
        if tmpdir:
            for z in zips:
                try:
                    os.unlink(z)
                except OSError:
                    pass
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass
    print(f"catálogo: {r['funds']:,} instrumentos ({r['etc']:,} ETC) · "
          f"{r['listings']:,} líneas · {r['seconds']}s -> {a.db}")
    # Cero es lo normal. Si sube, ESMA ha empezado a publicar un grupo CFI que
    # este código no sabe traducir, y esos instrumentos se están cayendo.
    if r["unclassified"]:
        print(f"  ⚠ {r['unclassified']:,} sin clasificar: hay un grupo CFI nuevo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
