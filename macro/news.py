"""Titulares económicos (RSS de Google News) y su tono respecto al ciclo.

El tono es deliberadamente simple: cuenta palabras ENTERAS de un léxico corto
de ciclo —recesión, despidos, crisis frente a crecimiento, contratación,
máximos— en los titulares de los últimos días. No pretende leer las noticias;
dice hacia qué lado empujan los titulares que más circulan. Lo que pesa de
verdad en el pilar de noticias son los índices de prensa de FRED (EPU, EMV),
construidos contando artículos durante décadas.

Los titulares son texto de terceros: se guardan y se pintan como TEXTO, nunca
como HTML, y del RSS sólo se leen título, medio, enlace y fecha.

GDELT, la fuente obvia, se descartó el 2026-10-18: desde este servidor responde
«limit requests to one every 5 seconds» incluso a la primera petición.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from .sources import Cache

QUERY = '(economy OR recession OR "Federal Reserve" OR inflation OR "jobs report" OR "stock market") when:3d'
RSS_URL = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

NEGATIVE = ["recession", "recessions", "crisis", "crash", "crashes", "layoff", "layoffs", "job cuts",
            "slump", "plunge", "plunges", "selloff", "sell-off", "default", "defaults", "bankruptcy",
            "contraction", "downturn", "slowdown", "stagflation", "bear market", "turmoil", "panic",
            "fear", "fears", "worries", "warning", "weak", "weakens", "slowing", "tumble", "tumbles",
            "sinks", "slides", "losses", "struggle", "struggles", "wobble", "worse", "risk", "risks",
            "warn", "warns"]
POSITIVE = ["growth", "rally", "rallies", "record high", "record highs", "hiring", "rebound",
            "rebounds", "expansion", "boom", "soft landing", "beats expectations", "grows", "strong",
            "gains", "jumps", "soars", "optimism", "beats", "upbeat"]
_NEG = re.compile(r"\b(" + "|".join(map(re.escape, NEGATIVE)) + r")\b", re.I)
_POS = re.compile(r"\b(" + "|".join(map(re.escape, POSITIVE)) + r")\b", re.I)


class NewsError(RuntimeError):
    pass


def parse_rss(text: str) -> list[dict]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise NewsError(f"RSS ilegible: {e}") from e
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise NewsError("no es un RSS")
    out = []
    for it in channel.findall("item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        # Google News añade « - Medio» al titular; se quita para no repetirlo.
        # El medio puede llevar guiones él mismo («ABC News - Breaking News»):
        # primero se busca su nombre entero al final, y sólo sin él se corta
        # por el último guion.
        if source and title.endswith(" - " + source):
            title = title[:-len(" - " + source)].strip()
        else:
            head, sep, tail = title.rpartition(" - ")
            if sep and not source:
                title, source = head.strip(), tail.strip()
        try:
            date = parsedate_to_datetime(it.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            date = None
        if title:
            out.append({"title": title, "source": source, "link": (it.findtext("link") or "").strip(),
                        "date": date})
    return out


def tone(items: list[dict]) -> dict:
    neg = pos = 0
    rows = []
    for h in items:
        n, p = len(_NEG.findall(h["title"])), len(_POS.findall(h["title"]))
        neg, pos = neg + n, pos + p
        rows.append({**h, "mood": (p > n) - (n > p)})
    score = (pos - neg) / (pos + neg) if pos + neg else None
    return {"score": score, "neg": neg, "pos": pos, "n": len(items), "headlines": rows}


def fetch_headlines(cache: Cache) -> tuple[list[dict], object]:
    url = RSS_URL.format(q=urllib.parse.quote(QUERY))
    got = cache.get("gnews-economy", url, parse_rss)
    return parse_rss(got.text), got
