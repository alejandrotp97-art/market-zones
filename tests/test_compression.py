"""Pages and static files travel gzip-compressed: the SSH tunnel bypasses Caddy's encode."""
import gzip
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D

GZ = {"Accept-Encoding": "gzip, deflate, br"}
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


@pytest.fixture
def client():
    D.app.config["TESTING"] = True
    return D.app.test_client()


def static_url(client, name):
    page = client.get("/cartera").get_data(as_text=True)
    return re.search(rf'"(/static/{re.escape(name)}\?v=[^"]+)"', page).group(1)


def test_static_js_is_gzipped_and_keeps_its_year_long_cache(client):
    url = static_url(client, "cartera.js")
    r = client.get(url, headers=GZ)
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in r.headers["Vary"]
    with open(os.path.join(STATIC, "cartera.js"), "rb") as fh:
        assert gzip.decompress(r.data) == fh.read()
    assert int(r.headers["Content-Length"]) == len(r.data) < os.path.getsize(os.path.join(STATIC, "cartera.js")) / 2
    assert "max-age=31536000" in r.headers["Cache-Control"]


def test_pages_are_gzipped(client):
    r = client.get("/cartera", headers=GZ)
    assert r.headers["Content-Encoding"] == "gzip"
    assert b"</html>" in gzip.decompress(r.data)


def test_clients_without_gzip_get_plain_bytes(client):
    r = client.get(static_url(client, "cartera.js"))
    assert "Content-Encoding" not in r.headers
    with open(os.path.join(STATIC, "cartera.js"), "rb") as fh:
        assert r.data == fh.read()


def test_a_revalidation_still_answers_304(client):
    url = static_url(client, "cartera.js")
    etag = client.get(url, headers=GZ).headers["ETag"]
    r = client.get(url, headers={**GZ, "If-None-Match": etag})
    assert r.status_code == 304 and r.data == b""


def test_the_plain_and_gzip_variants_have_different_etags(client):
    url = static_url(client, "cartera.js")
    assert client.get(url, headers=GZ).headers["ETag"] != client.get(url).headers["ETag"]


def test_tiny_responses_are_left_alone(client):
    r = client.get("/api/search?q=a", headers=GZ)
    assert "Content-Encoding" not in r.headers and r.get_json() == {"results": []}
