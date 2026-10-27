"""Every response tells the browser to run only this app's scripts and never be framed.

Pages are rendered by Flask's test client; /swing's upstream is faked, nothing
leaves the machine and no database is touched.
"""
import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D

PAGES = ["/", "/screener", "/comite", "/regime", "/validation"]


@pytest.fixture
def client():
    D.app.config["TESTING"] = True
    return D.app.test_client()


def policy(resp) -> dict:
    parts = (p.strip().split(" ", 1) for p in resp.headers["Content-Security-Policy"].split(";") if p.strip())
    return {p[0]: (p[1] if len(p) > 1 else "") for p in parts}


def nonce_of(resp) -> str:
    return re.search(r"'nonce-([^']+)'", policy(resp)["script-src"]).group(1)


def inline_script_attrs(html: str) -> list:
    return re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", html)


@pytest.mark.parametrize("path", PAGES)
def test_pages_get_a_strict_policy_and_hardening_headers(client, path):
    r = client.get(path)
    assert r.status_code == 200
    p = policy(r)
    assert p["default-src"] == "'self'"
    assert p["script-src"].startswith("'self' 'nonce-")
    assert "'unsafe-inline'" not in p["script-src"] and "'unsafe-eval'" not in p["script-src"]
    assert p["frame-ancestors"] == "'none'" and p["object-src"] == "'none'" and p["base-uri"] == "'none'"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "same-origin"  # no-referrer makes POSTs send Origin: null


@pytest.mark.parametrize("path", ["/screener", "/comite"])
def test_inline_scripts_carry_the_nonce_of_their_response(client, path):
    r = client.get(path)
    attrs = inline_script_attrs(r.get_data(as_text=True))
    assert attrs, "these pages have an inline script"
    assert all(f'nonce="{nonce_of(r)}"' in a for a in attrs), attrs


def test_each_response_gets_a_fresh_nonce(client):
    a, b = nonce_of(client.get("/screener")), nonce_of(client.get("/screener"))
    assert a != b and len(a) >= 16


class _Upstream(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_swing_keeps_its_inline_script_by_getting_the_nonce(client, monkeypatch):
    page = b"<html><head><style>b{}</style></head><body><script>draw()</script></body></html>"
    monkeypatch.setattr(D.urllib.request, "urlopen", lambda req, timeout=None: _Upstream(page))
    r = client.get("/swing")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f'<script nonce="{nonce_of(r)}">draw()</script>' in html
    assert policy(r)["frame-ancestors"] == "'none'"


def test_third_party_static_html_is_not_broken_but_cannot_be_framed(client):
    r = client.get("/abraham/index.html")
    p = policy(r)
    assert "script-src" not in p  # its generated pages are not ours to rewrite
    assert p["frame-ancestors"] == "'none'" and p["object-src"] == "'none'"
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"


def test_api_json_gets_the_headers_too(client):
    r = client.get("/api/does-not-exist")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert policy(r)["frame-ancestors"] == "'none'"


def test_swing_gives_up_on_a_slow_upstream_quickly(client, monkeypatch):
    """A hung :8775 must not hold one of the 8 waitress threads for 30 s."""
    seen = {}

    def slow(req, timeout=None):
        seen["timeout"] = timeout
        raise TimeoutError("timed out")

    monkeypatch.setattr(D.urllib.request, "urlopen", slow)
    r = client.get("/swing")
    assert r.status_code == 503 and "no disponible" in r.get_data(as_text=True)
    assert seen["timeout"] <= 8
