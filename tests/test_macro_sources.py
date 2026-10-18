"""Fuentes del ciclo: el parseo de FRED y de la OCDE y la caché en disco.

Lo que se protege: que un día sin dato no se lea como cero, que las filas
desordenadas de la OCDE salgan en orden, y que si la red falla se sirva la copia
de disco marcada como vieja — y que una página de error nunca pise la buena.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from macro import sources as S

FRED_TXT = "observation_date,VIXCLS\n2026-09-02,15.20\n2026-09-01,16.34\n2026-09-03,\n2026-09-04,.\n"
OECD_TXT = (
    "DATAFLOW,REF_AREA,FREQ,MEASURE,TIME_PERIOD,OBS_VALUE\n"
    "X,USA,M,LI,2026-05,100.65\n"
    "X,G20,M,LI,2026-09,100.14\n"
    "X,USA,M,LI,2026-04,100.54\n"
    "X,USA,M,LI,2026-06,NaN-ish\n"
)


def test_fred_descarta_huecos_y_ordena():
    assert S.parse_fred_csv(FRED_TXT) == [("2026-09-01", 16.34), ("2026-09-02", 15.20)]


def test_fred_rechaza_lo_que_no_es_una_serie():
    with pytest.raises(S.SourceError):
        S.parse_fred_csv("<html>error</html>")


def test_oecd_agrupa_por_pais_ordena_y_fija_el_dia_uno():
    d = S.parse_oecd_csv(OECD_TXT)
    assert d["USA"] == [("2026-04-01", 100.54), ("2026-05-01", 100.65)]
    assert d["G20"] == [("2026-09-01", 100.14)]


class Reloj:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_cache_sirve_de_disco_mientras_no_caduca(tmp_path):
    llamadas = []
    reloj = Reloj(1000.0)
    c = S.Cache(str(tmp_path), ttl=100, getter=lambda u: llamadas.append(u) or FRED_TXT, now=reloj)
    serie, got = S.fred("VIXCLS", c)
    assert serie[0] == ("2026-09-01", 16.34) and not got.stale
    reloj.t = 1050.0
    S.fred("VIXCLS", c)
    assert len(llamadas) == 1                       # la segunda vino de disco
    assert "id=VIXCLS" in llamadas[0] and "cosd=2000-01-01" in llamadas[0]


def test_si_la_red_falla_sirve_la_copia_vieja_marcada(tmp_path):
    reloj = Reloj(1000.0)
    S.fred("VIXCLS", S.Cache(str(tmp_path), ttl=100, getter=lambda u: FRED_TXT, now=reloj))
    reloj.t = 5000.0

    def caida(url):
        raise OSError("timeout")

    serie, got = S.fred("VIXCLS", S.Cache(str(tmp_path), ttl=100, getter=caida, now=reloj))
    assert got.stale and got.fetched_at == 1000.0
    assert serie[-1] == ("2026-09-02", 15.20)


def test_una_pagina_de_error_no_pisa_la_copia_buena(tmp_path):
    reloj = Reloj(1000.0)
    S.fred("VIXCLS", S.Cache(str(tmp_path), ttl=100, getter=lambda u: FRED_TXT, now=reloj))
    reloj.t = 5000.0
    _, got = S.fred("VIXCLS", S.Cache(str(tmp_path), ttl=100, getter=lambda u: "<html>", now=reloj))
    assert got.stale
    _, otra = S.fred("VIXCLS", S.Cache(str(tmp_path), ttl=10**9, getter=lambda u: "<html>", now=reloj))
    assert otra.text == FRED_TXT                    # en disco sigue la buena


def test_sin_red_y_sin_copia_es_un_error_explicito(tmp_path):
    def caida(url):
        raise OSError("timeout")

    with pytest.raises(S.SourceError, match="fred-VIXCLS"):
        S.fred("VIXCLS", S.Cache(str(tmp_path), getter=caida))
