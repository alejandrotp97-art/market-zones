"""Descarga de series macro: FRED (CSV público) y OCDE (SDMX), sin clave de API.

Ninguna de las dos pide clave para estos puntos de acceso:

- FRED `fredgraph.csv?id=X&cosd=AAAA-MM-DD` devuelve `observation_date,X`. Con
  VARIOS ids a la vez devuelve un ZIP, así que se pide una serie por petición.
  Los días sin dato llegan con el valor vacío (antes era un punto): ambos se
  descartan, nunca se leen como cero.
- OCDE `DSD_STES@DF_CLI` es el indicador compuesto adelantado (CLI), ajustado
  en amplitud: la tendencia de largo plazo vale 100. Las filas llegan SIN orden.

Cada respuesta se guarda en disco. Si la red falla se sirve la última copia,
por vieja que sea, y se dice que es vieja: un panel con el dato de ayer es útil;
uno en blanco porque FRED tardó, no.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
OECD_CLI_URL = ("https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI,"
                "/{areas}.M.LI...AA...H?startPeriod={start}&format=csv")
USER_AGENT = "market-zones/ciclo (+https://fred.stlouisfed.org)"
DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "macro")

Series = list[tuple[str, float]]          # [(fecha ISO, valor)], ordenada por fecha


class SourceError(RuntimeError):
    """Ni la red ni la caché tienen la serie."""


def http_get(url: str, timeout: float = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def parse_fred_csv(text: str) -> Series:
    rows = csv.reader(io.StringIO(text))
    header = next(rows, None)
    if not header or header[0] != "observation_date":
        raise SourceError("FRED no devolvió un CSV de serie")
    out = []
    for row in rows:
        if len(row) < 2 or row[1] in ("", "."):
            continue
        try:
            out.append((row[0], float(row[1])))
        except ValueError:
            continue
    out.sort()
    return out


def parse_oecd_csv(text: str) -> dict[str, Series]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "OBS_VALUE" not in reader.fieldnames:
        raise SourceError("La OCDE no devolvió un CSV SDMX")
    out: dict[str, Series] = {}
    for row in reader:
        try:
            v = float(row["OBS_VALUE"])
        except (TypeError, ValueError):
            continue
        # El periodo mensual llega como AAAA-MM; se fija al día 1 para que
        # compare con las fechas diarias de FRED.
        out.setdefault(row["REF_AREA"], []).append((row["TIME_PERIOD"] + "-01", v))
    for s in out.values():
        s.sort()
    return out


@dataclass
class Fetched:
    text: str
    fetched_at: float          # epoch de la descarga que produjo `text`
    stale: bool                # True: la red falló y esto es la copia de disco


class Cache:
    """Caché de texto en disco con caducidad; sirve la copia vieja si la red falla."""

    def __init__(self, directory: str = DEFAULT_DIR, ttl: float = 6 * 3600,
                 getter: Callable[[str], str] = http_get, now: Callable[[], float] = time.time):
        self.dir, self.ttl, self.getter, self.now = directory, ttl, getter, now

    def _path(self, key: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
        return os.path.join(self.dir, safe + ".json")

    def _read(self, key: str) -> Fetched | None:
        try:
            with open(self._path(key), encoding="utf-8") as f:
                d = json.load(f)
            return Fetched(d["text"], float(d["fetched_at"]), False)
        except (OSError, ValueError, KeyError):
            return None

    def get(self, key: str, url: str, validate: Callable[[str], object]) -> Fetched:
        cached = self._read(key)
        if cached and self.now() - cached.fetched_at < self.ttl:
            return cached
        try:
            text = self.getter(url)
            validate(text)                 # una página de error no pisa la copia buena
        except Exception as e:
            if cached:
                cached.stale = True
                return cached
            raise SourceError(f"{key}: {e}") from e
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._path(key) + ".parcial"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"text": text, "fetched_at": self.now(), "url": url}, f)
        os.replace(tmp, self._path(key))
        return Fetched(text, self.now(), False)


def fred(sid: str, cache: Cache, start: str = "2000-01-01") -> tuple[Series, Fetched]:
    got = cache.get(f"fred-{sid}", FRED_URL.format(sid=sid, start=start), parse_fred_csv)
    return parse_fred_csv(got.text), got


def oecd_cli(cache: Cache, areas: tuple[str, ...] = ("USA", "G20"),
             start: str = "2000-01") -> tuple[dict[str, Series], Fetched]:
    url = OECD_CLI_URL.format(areas="+".join(areas), start=start)
    got = cache.get("oecd-cli-" + "-".join(areas), url, parse_oecd_csv)
    return parse_oecd_csv(got.text), got
