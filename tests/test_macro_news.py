"""Noticias del ciclo: titulares de Google News y su tono, y los índices de prensa.

Lo que se protege: que el RSS se lea sin ejecutar nada de lo que trae (es texto
de terceros), que el tono cuente palabras enteras —«crisis» sí, «crisp» no— y
que el pilar de noticias exista con sus tres patas, aunque falte alguna.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from macro import news as N
from macro import pillars as P

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>x</title>
<item><title>Recession fears grow as layoffs mount - Reuters</title><link>https://n/1</link>
<pubDate>Sun, 18 Oct 2026 10:00:00 GMT</pubDate><source url="https://reuters.com">Reuters</source></item>
<item><title>Stocks rally to record high on strong hiring - CNBC</title><link>https://n/2</link>
<pubDate>Sat, 17 Oct 2026 09:00:00 GMT</pubDate><source url="https://cnbc.com">CNBC</source></item>
<item><title>Crisp autumn weather &lt;b&gt;ahead&lt;/b&gt; - Local</title><link>https://n/3</link>
<pubDate>Sat, 17 Oct 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_rss_saca_titular_fuente_enlace_y_fecha_sin_el_sufijo_del_medio():
    items = N.parse_rss(RSS)
    assert items[0] == {"title": "Recession fears grow as layoffs mount", "source": "Reuters",
                        "link": "https://n/1", "date": "2026-10-18"}
    assert items[2]["title"] == "Crisp autumn weather <b>ahead</b>"     # texto, nunca HTML
    assert items[2]["source"] == "Local"


def test_rss_roto_es_un_error_y_no_una_lista_vacia():
    with pytest.raises(N.NewsError):
        N.parse_rss("<html>captcha</html>")


def test_tono_cuenta_palabras_enteras():
    t = N.tone(N.parse_rss(RSS))
    # recession+fears+layoffs («grow» no es «grows») / rally+record high+strong+hiring
    assert (t["neg"], t["pos"], t["n"]) == (3, 4, 3)
    assert t["score"] == pytest.approx((4 - 3) / 7)
    assert [h["mood"] for h in t["headlines"]] == [-1, 1, 0]


def test_tono_sin_palabras_de_ciclo_es_desconocido_no_neutro():
    assert N.tone([{"title": "Autumn weather", "source": "", "link": "", "date": "2026-10-18"}])["score"] is None


def test_media_de_30_dias():
    s = [("2026-01-01", 900.0), ("2026-02-01", 100.0), ("2026-02-20", 200.0)]
    assert P.transform(s, "avg30", "2026-02-20") == pytest.approx(150.0)


def test_el_pilar_de_noticias_tiene_sus_tres_patas():
    keys = [i.key for i in P.INDICATORS if i.pillar == "noticias"]
    assert keys == ["epu", "emv", "headlines"]
    data = {"NEWS_TONE": [("2026-10-18", -0.5)]}
    pil = {p["key"]: p for p in P.evaluate(data, "2026-10-18")["pillars"]}
    assert pil["noticias"]["score"] == pytest.approx(P.score(P.BY_KEY["headlines"], -0.5))


def test_el_sufijo_se_quita_aunque_el_medio_lleve_guiones():
    rss = RSS.replace("Recession fears grow as layoffs mount - Reuters</title>",
                      "Fed warns - ABC News - Breaking News</title>").replace(
        "<source url=\"https://reuters.com\">Reuters</source>",
        "<source url=\"https://abc.com\">ABC News - Breaking News</source>")
    assert N.parse_rss(rss)[0]["title"] == "Fed warns"
