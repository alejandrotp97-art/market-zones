"""El nombre del instrumento sale del payload que YA se descargó.

Antes el panel abría una SEGUNDA conexión al mismo endpoint de Yahoo sólo para
leer `longName`: 12 s de timeout encadenados detrás de los 30 s del histórico,
o sea 42 s en el peor caso contra un abort de cliente de 25 s. Y como el fallo
no se cacheaba, una degradación parcial de Yahoo hacía repagar esos 12 s en cada
refresco de caché, de cada símbolo.

Lo que estos tests protegen: que el nombre siga saliendo del `meta` que viaja en
la respuesta del histórico, y que un `meta` ausente o roto degrade a cadena
vacía en vez de reventar el build entero por un dato decorativo.
"""
import json
import os
import sys
import urllib.request

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D
from zones.data import fetch_daily


def test_name_comes_from_meta():
    assert D._name_from_meta({"longName": "Tesla, Inc."}) == "TESLA"
    assert D._name_from_meta({"longName": "Amazon.com, Inc."}) == "AMAZON"


def test_long_name_wins_over_short_name():
    meta = {"longName": "Tesla, Inc.", "shortName": "TSLA"}
    assert D._name_from_meta(meta) == "TESLA"


def test_short_name_is_the_fallback():
    assert D._name_from_meta({"shortName": "Vanguard S&P 500"}) == "VANGUARD S&P 500"


@pytest.mark.parametrize("meta", [None, {}, "no soy un dict", 42, [],
                                  {"longName": None, "shortName": None},
                                  {"longName": ""}])
def test_missing_or_broken_meta_degrades_to_empty(meta):
    """El nombre es decorativo: nunca puede tumbar el build del gráfico."""
    assert D._name_from_meta(meta) == ""


def test_fetch_daily_exposes_meta_without_extra_request(monkeypatch):
    """Una sola conexión, y el meta llega en `attrs` del frame devuelto."""
    payload = {"chart": {"result": [{
        "timestamp": [1704067200, 1704153600, 1704240000],
        "indicators": {"quote": [{"open": [1.0, 2.0, 3.0], "high": [1.0, 2.0, 3.0],
                                  "low": [1.0, 2.0, 3.0], "close": [1.0, 2.0, 3.0],
                                  "volume": [10, 20, 30]}]},
        "meta": {"longName": "Tesla, Inc.", "currency": "USD"},
    }]}}
    calls = []

    class _Resp:
        def read(self):
            return json.dumps(payload).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake(req, *a, **k):
        calls.append(getattr(req, "full_url", str(req)))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    df = fetch_daily("TSLA", years=1, drop_forming=False)

    assert len(calls) == 1, "el histórico debe bastar: nada de una segunda llamada"
    assert df.attrs["meta"]["longName"] == "Tesla, Inc."
    assert D._name_from_meta(df.attrs.get("meta")) == "TESLA"
    # `>=` entre listas compara lexicográficamente, NO es contención: pasaba de
    # casualidad porque las dos listas resultaban iguales.
    assert set(df.columns) >= {"open", "high", "low", "close", "volume", "date"}


def test_meta_is_present_even_when_yahoo_omits_it(monkeypatch):
    """Sin `meta` el frame sigue siendo válido y el nombre queda vacío."""
    payload = {"chart": {"result": [{
        "timestamp": [1704067200, 1704153600],
        "indicators": {"quote": [{"open": [1.0, 2.0], "high": [1.0, 2.0],
                                  "low": [1.0, 2.0], "close": [1.0, 2.0],
                                  "volume": [10, 20]}]},
    }]}}

    class _Resp:
        def read(self):
            return json.dumps(payload).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    df = fetch_daily("TSLA", years=1, drop_forming=False)
    assert df.attrs["meta"] == {}
    assert D._name_from_meta(df.attrs.get("meta")) == ""
    assert len(df) == 2


# ── el cableado dentro de _build ────────────────────────────────────────────
# Los tests de arriba cubren las dos MITADES por separado: la función pura y el
# `attrs` que expone fetch_daily. Ninguno cubre el punto donde se unen, así que
# cambiar la clave del attrs, capturar el nombre después del resample o dejar de
# meter `name` en el dict pasaba la suite entera sin enterarse.

def _frame_with_meta(n=600, meta=None):
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))
    df = pd.DataFrame({"date": pd.bdate_range("2019-01-01", periods=n),
                       "open": close, "high": close * 1.01, "low": close * 0.99,
                       "close": close,
                       "volume": rng.integers(1e6, 5e6, n).astype(float)})
    df.attrs["meta"] = {"longName": "Tesla, Inc."} if meta is None else meta
    return df


@pytest.mark.parametrize("tf", ["daily", "weekly"])
def test_build_puts_the_name_in_the_payload(monkeypatch, tf):
    """El nombre llega al payload en los DOS marcos temporales."""
    monkeypatch.setattr(D, "fetch_daily", lambda *a, **k: _frame_with_meta())
    out = D._build("TSLA", 0.10, tf)
    assert out["name"] == "TESLA"
    assert out["tf"] == tf


def test_build_keeps_the_name_for_futures(monkeypatch):
    """La rama `=F` tira la columna de volumen; el nombre no puede irse con ella."""
    monkeypatch.setattr(D, "fetch_daily", lambda *a, **k: _frame_with_meta())
    assert D._build("BZ=F", 0.10, "daily")["name"] == "TESLA"


def test_build_survives_a_frame_without_meta(monkeypatch):
    """Sin meta el gráfico se sirve igual: el nombre es decorativo."""
    monkeypatch.setattr(D, "fetch_daily", lambda *a, **k: _frame_with_meta(meta={}))
    out = D._build("TSLA", 0.10, "daily")
    assert out["name"] == ""
    assert out["summary"]["score"] is not None      # el gráfico sigue ahí
