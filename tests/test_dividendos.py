"""Calendario de dividendos cobrados y previsión de los próximos 12 meses.

La previsión repite, un año más tarde, cada pago que el instrumento hizo en los
últimos 12 meses, por las participaciones que tienes HOY y al cambio de hoy. Es
una estimación y la respuesta lo dice; lo que se protege aquí es que no invente
pagos (un instrumento que dejó de pagar no proyecta nada) y que cuadren los
meses, las divisas y las cantidades.
"""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D
from cartera import dividends as V
from zones import data as Z

HOY = dt.date(2026, 10, 16)


def test_cada_pago_del_ultimo_anyo_se_repite_un_anyo_despues():
    ev = {"AAA": [{"date": "2025-12-10", "amount": 0.5}, {"date": "2026-06-10", "amount": 0.6},
                  {"date": "2024-06-10", "amount": 9.9}]}          # viejo: fuera
    hold = {"AAA": {"qty": 100.0, "fx": 0.9, "name": "Alfa", "market_value": 9000.0}}
    f = V.forecast(hold, ev, HOY)
    assert [m["month"] for m in f["months"]][:3] == ["2026-11", "2026-12", "2027-01"]
    assert len(f["months"]) == 12
    dic = next(m for m in f["months"] if m["month"] == "2026-12")
    assert dic["eur"] == pytest.approx(0.5 * 100 * 0.9)
    jun = next(m for m in f["months"] if m["month"] == "2027-06")
    assert jun["eur"] == pytest.approx(0.6 * 100 * 0.9)
    assert f["total"] == pytest.approx((0.5 + 0.6) * 90)
    assert f["by_ticker"][0]["yield"] == pytest.approx((0.5 + 0.6) * 90 / 9000)


def test_quien_dejo_de_pagar_no_proyecta_nada():
    ev = {"AAA": [{"date": "2024-03-01", "amount": 1.0}]}
    hold = {"AAA": {"qty": 10.0, "fx": 1.0, "name": "Alfa", "market_value": 100.0}}
    f = V.forecast(hold, ev, HOY)
    assert f["total"] == 0 and f["by_ticker"] == []


def test_lo_que_no_tienes_no_proyecta_y_sin_fx_se_dice():
    ev = {"AAA": [{"date": "2026-01-10", "amount": 1.0}],
          "BBB": [{"date": "2026-01-10", "amount": 1.0}]}
    hold = {"BBB": {"qty": 5.0, "fx": None, "name": "Beta", "market_value": None}}
    f = V.forecast(hold, ev, HOY)
    assert f["total"] == 0
    assert f["no_fx"] == ["BBB"]


def test_cobrados_por_mes_rellena_los_meses_vacios():
    idx = pd.to_datetime(["2026-07-15", "2026-08-14", "2026-09-15", "2026-10-15"])
    pos = {"AAA": {"divs": np.array([10.0, 0, 5.0, 0])},
           "BBB": {"divs": np.array([2.0, 0, 0, 0])}}
    r = V.monthly_received(idx, pos, {"AAA": "Alfa"}, months=24)
    assert [m["month"] for m in r] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert r[0]["eur"] == pytest.approx(12.0)
    assert r[0]["by"][0] == {"ticker": "AAA", "name": "Alfa", "eur": 10.0}
    assert r[1]["eur"] == 0


def test_yahoo_dividendos_se_leen_de_la_misma_respuesta():
    ev = {"dividends": {"1": {"amount": 0.07, "date": 1741852800},
                        "2": {"amount": 0.0615, "date": 1733990400},
                        "3": {"amount": None, "date": 1}}}
    out = Z._parse_dividends(ev)
    assert out == [{"date": "2024-12-12", "amount": 0.0615},
                   {"date": "2025-03-13", "amount": 0.07}]
    assert Z._parse_dividends(None) == []


# ── la ruta ───────────────────────────────────────────────────────────────
@pytest.fixture
def libro(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "CARTERA_DB", str(tmp_path / "cartera.db"))
    monkeypatch.setattr(D, "_instrument_ccy", lambda s: "EUR")
    monkeypatch.setattr(D, "_fx_series_eur", lambda c: None)
    monkeypatch.setattr(D, "_fx_now", lambda c: 1.0)
    monkeypatch.setattr(D, "_last_price", lambda t: 100.0)
    monkeypatch.setattr(D, "_seed_geo_async", lambda *a, **k: None)
    monkeypatch.setattr(D, "_quote_meta", lambda t: (100.0, "EUR"))
    monkeypatch.setattr(D, "_prefetch", lambda fn, ts: None)
    monkeypatch.setattr(D, "PUBLIC_HOST", "")
    return D.app.test_client()


def test_ruta_dividendos(libro, monkeypatch):
    end = pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=60)
    monkeypatch.setattr(D, "_close_series", lambda t: pd.Series([100.0] * 60, index=idx))
    hace = (end - pd.Timedelta(days=200)).date().isoformat()
    monkeypatch.setattr(D, "_dividends_of", lambda t: [{"date": hace, "amount": 2.0}])
    for body in ({"ticker": "AAA", "side": "buy", "quantity": 10, "price": 100,
                  "date": idx[0].date().isoformat(), "name": "Alfa"},
                 {"ticker": "AAA", "side": "div", "price": 15, "fee": 3,
                  "date": idx[30].date().isoformat()}):
        assert libro.post("/api/cartera", json=body, headers={D.CSRF_HEADER: "1"}).status_code == 200

    d = libro.get("/api/cartera/dividendos").get_json()

    assert sum(m["eur"] for m in d["received"]) == pytest.approx(12.0)       # neto
    assert d["forecast"]["total"] == pytest.approx(2.0 * 10)
    assert d["forecast"]["by_ticker"][0]["name"] == "Alfa"
    assert d["received_12m"] == pytest.approx(12.0)
    assert "estimación" in d["note"]


def test_ruta_dividendos_sin_movimientos(libro):
    d = libro.get("/api/cartera/dividendos").get_json()
    assert d["received"] == [] and d["forecast"]["total"] == 0
