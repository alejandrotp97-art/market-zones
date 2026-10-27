"""An expired cache entry is answered at once and refreshed behind the visitor's back.

Before: after CACHE_TTL (600 s) every symbol went cold again and the next visitor
waited 1-1.7 s for Yahoo inside the request. Now a stale entry younger than
STALE_MAX is served immediately and ONE background refresh replaces it. Nothing
here touches the network: builds and fetch_daily are fakes.
"""
import json
import os
import sys
import threading
import time

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


@pytest.fixture(autouse=True)
def clean():
    D._upstream = D.TokenBucket(1000.0, 1000)
    D._cache.clear(); D._cache_bytes = 0; D._series_cache.clear()
    yield
    D._wait_refreshes(5)
    D._cache.clear(); D._cache_bytes = 0; D._series_cache.clear()


def put(key, payload, age):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    with D._lock:
        D._payload_put(key, raw, D.gzip.compress(raw))
        ts, r, g = D._cache[key]
        D._cache[key] = (time.time() - age, r, g)


def value(key):
    return json.loads(D._cache[key][1])


def test_a_stale_entry_is_served_at_once_and_refreshed_in_the_background():
    put("k", {"v": "old"}, age=D.CACHE_TTL + 5)
    started, release, calls = threading.Event(), threading.Event(), []

    def build():
        calls.append(1); started.set(); release.wait(5)
        return {"v": "new"}

    t0 = time.time()
    raw, _ = D._encoded("k", build)
    assert json.loads(raw) == {"v": "old"} and time.time() - t0 < 0.5
    assert started.wait(2), "no background refresh started"
    D._encoded("k", build)                       # a second stale hit: no second refresh
    release.set(); D._wait_refreshes(5)
    assert len(calls) == 1 and value("k") == {"v": "new"}
    assert json.loads(D._encoded("k", build)[0]) == {"v": "new"} and len(calls) == 1


def test_a_fresh_entry_does_not_refresh():
    put("k", {"v": "fresh"}, age=1)
    calls = []
    D._encoded("k", lambda: (calls.append(1), {"v": "x"})[1])
    D._wait_refreshes(2)
    assert calls == []


def test_an_entry_older_than_stale_max_is_rebuilt_in_the_request():
    put("k", {"v": "ancient"}, age=D.STALE_MAX + 5)
    raw, _ = D._encoded("k", lambda: {"v": "new"})
    assert json.loads(raw) == {"v": "new"}


def test_a_failed_refresh_keeps_the_stale_entry():
    put("k", {"v": "old"}, age=D.CACHE_TTL + 5)

    def boom():
        raise RuntimeError("yahoo down")

    assert json.loads(D._encoded("k", boom)[0]) == {"v": "old"}
    D._wait_refreshes(5)
    assert value("k") == {"v": "old"}


def test_a_refresh_past_the_rate_budget_is_skipped_not_raised():
    put("k", {"v": "old"}, age=D.CACHE_TTL + 5)
    D._upstream = D.TokenBucket(0.0001, 0); calls = []
    assert json.loads(D._encoded("k", lambda: (calls.append(1), {})[1], limited=True)[0]) == {"v": "old"}
    D._wait_refreshes(5)
    assert calls == [] and value("k") == {"v": "old"}


def frame(price):
    return pd.DataFrame({"date": pd.to_datetime(["2026-10-23", "2026-10-26"]), "close": [price, price]})


def test_portfolio_price_series_are_also_served_stale_and_refreshed(monkeypatch):
    calls = []
    monkeypatch.setattr(D, "fetch_daily", lambda t, years=25: (calls.append(t), frame(20.0))[1])
    old = pd.Series([10.0], index=pd.DatetimeIndex(["2026-10-23"]))
    D._series_cache["AAA"] = (time.time() - 605, old, [], [])
    assert D._close_series("AAA").iloc[-1] == 10.0      # stale, at once
    D._wait_refreshes(5)
    assert calls == ["AAA"] and D._close_series("AAA").iloc[-1] == 20.0 and calls == ["AAA"]


def test_a_missing_series_is_still_fetched_in_the_request(monkeypatch):
    monkeypatch.setattr(D, "fetch_daily", lambda t, years=25: frame(30.0))
    assert D._close_series("BBB").iloc[-1] == 30.0
