"""V5 — the expensive path must not be free to trigger in a loop.

Loopback binding and the Host check stop a foreign page from READING a
response, but a cross-origin GET still reaches the service: the browser only
hides the answer. So any page the user opens can make this server fetch from
Yahoo and burn CPU as fast as it likes — as can a runaway script of their own.

The design point under test: only a cache MISS is charged. Normal browsing is
served from cache and must never meet the limiter.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


def _fresh_bucket(rate=1.0, burst=3):
    D._upstream = D.TokenBucket(rate, burst)
    D._cache.clear()
    D._cache_bytes = 0
    return D._upstream


def test_bucket_allows_the_burst_then_refuses():
    b = _fresh_bucket(rate=1.0, burst=3)
    assert [b.take() for _ in range(3)] == [True, True, True]
    assert b.take() is False


def test_bucket_refills_over_time():
    b = _fresh_bucket(rate=50.0, burst=2)
    assert b.take() and b.take()
    assert b.take() is False
    time.sleep(0.1)                      # ~5 tokens at 50/s
    assert b.take() is True


def test_bucket_never_exceeds_its_burst():
    b = _fresh_bucket(rate=1000.0, burst=2)
    time.sleep(0.05)                     # would be 50 tokens without the cap
    assert b.take() and b.take()
    assert b.take() is False


def test_a_cache_hit_is_never_charged():
    """The whole design: browsing a warm panel must not consume the budget."""
    b = _fresh_bucket(rate=0.0001, burst=1)
    calls = []
    build = lambda: (calls.append(1), {"x": 1})[1]
    D._encoded("k", build, limited=True)          # miss: spends the only token
    assert len(calls) == 1 and b.take() is False
    for _ in range(50):                            # hits: free
        raw, gz = D._encoded("k", build, limited=True)
    assert len(calls) == 1, "a cache hit rebuilt or was charged"
    D._cache.clear()
    D._cache_bytes = 0


def test_a_miss_past_the_budget_raises_instead_of_building():
    _fresh_bucket(rate=0.0001, burst=1)
    calls = []
    build = lambda: (calls.append(1), {"x": 1})[1]
    D._encoded("a", build, limited=True)
    try:
        D._encoded("b", build, limited=True)
        assert False, "the second miss was built anyway"
    except D.TooBusy:
        pass
    assert len(calls) == 1, "build ran despite the limiter"
    D._cache.clear()
    D._cache_bytes = 0


def test_unlimited_callers_are_exempt():
    """The prewarm calls the builders directly and must not be throttled, or a
    cold start would starve itself."""
    _fresh_bucket(rate=0.0001, burst=0)
    calls = []
    build = lambda: (calls.append(1), {"x": 1})[1]
    for i in range(5):
        D._encoded(f"p{i}", build, limited=False)
    assert len(calls) == 5
    D._cache.clear()
    D._cache_bytes = 0


def test_route_answers_429_with_retry_after():
    _fresh_bucket(rate=0.5, burst=0)
    D.app.config["TESTING"] = True
    cli = D.app.test_client()
    r = cli.get("/api/zones?symbol=NLR")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert "Espera" in r.get_json()["error"]
    _fresh_bucket()                       # leave the module usable


def test_a_bad_symbol_is_rejected_before_it_costs_a_token():
    """Validation must come first, or garbage input would drain the budget."""
    b = _fresh_bucket(rate=1.0, burst=5)
    D.app.config["TESTING"] = True
    cli = D.app.test_client()
    before = b._tokens
    assert cli.get("/api/zones?symbol=../../etc").status_code == 400
    assert b._tokens >= before - 0.01, "an invalid symbol spent a token"
    _fresh_bucket()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
