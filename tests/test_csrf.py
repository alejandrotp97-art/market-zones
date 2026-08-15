"""CSRF gate on every state-changing route.

The service has no session and binds to loopback, which stops the NETWORK but
not the user's own browser: any page they open while the tunnel is up can POST
here. A cross-site form is a CORS "simple request", so no preflight fires — and
`POST /api/cartera/clear` with an empty body wipes the portfolio.

These tests run against Flask's test client on a THROWAWAY database. They never
touch the real cartera.db.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D

HDR = {D.CSRF_HEADER: "1"}
EVIL = "https://evil.example"


def _client():
    """A test client wired to an empty, disposable database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    D.CARTERA_DB = path
    with D._cartera_conn() as c:
        c.execute("DELETE FROM movements")
        c.execute("INSERT INTO movements(date,ticker,side,quantity,price,fee)"
                  " VALUES('2024-01-01','TEST','buy',10,100,0)")
    D.app.config["TESTING"] = True
    return D.app.test_client(), path


def _count(path):
    import sqlite3
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT COUNT(*) FROM movements").fetchone()[0]
    finally:
        c.close()


def test_cross_site_form_cannot_wipe_the_portfolio():
    """The exact attack: an auto-submitting form on any page the user visits."""
    cli, path = _client()
    r = cli.post("/api/cartera/clear",
                 headers={"Origin": EVIL,
                          "Content-Type": "application/x-www-form-urlencoded"},
                 data="")
    assert r.status_code == 403, "a cross-site form still reaches /clear"
    assert _count(path) == 1, "the portfolio was wiped"
    os.unlink(path)


def test_cross_site_text_plain_cannot_insert():
    """`get_json(force=True)` ignores the Content-Type, so a form posting
    text/plain — a simple request, no preflight — used to reach the JSON API."""
    cli, path = _client()
    r = cli.post("/api/cartera", headers={"Origin": EVIL, "Content-Type": "text/plain"},
                 data='{"ticker":"EVIL","quantity":1,"price":1}')
    assert r.status_code == 403
    assert _count(path) == 1
    os.unlink(path)


def test_cross_site_multipart_upload_is_rejected():
    import io
    cli, path = _client()
    csv = b"Fecha,Ticker,Tipo,Cantidad,Precio\n2024-01-01,EVIL,Compra,99,1\n"
    r = cli.post("/api/cartera/upload", headers={"Origin": EVIL},
                 data={"file": (io.BytesIO(csv), "evil.csv")},
                 content_type="multipart/form-data")
    assert r.status_code == 403
    assert _count(path) == 1
    os.unlink(path)


def test_header_alone_is_not_enough_from_a_foreign_origin():
    """Belt and braces: even if a header could be forged, the Origin must be
    loopback. This is also what stops a DNS-rebinding origin from writing."""
    cli, path = _client()
    r = cli.post("/api/cartera/clear", headers={**HDR, "Origin": EVIL})
    assert r.status_code == 403
    assert _count(path) == 1
    os.unlink(path)


def test_rebound_origin_cannot_write_even_though_it_is_same_origin():
    """The DNS-rebinding case, spelled out. After rebinding, the page IS
    same-origin: no CORS applies and it can set any header it likes, so the
    header barrier falls. What does not change is that its Origin is the
    attacker's name, not loopback."""
    cli, path = _client()
    rebound = "http://evil.example:8771"
    r = cli.post("/api/cartera/clear",
                 headers={**HDR, "Origin": rebound, "Host": "evil.example:8771"})
    assert r.status_code in (400, 403), "a rebound origin managed to write"
    assert _count(path) == 1
    os.unlink(path)


def test_foreign_referer_is_rejected_too():
    cli, path = _client()
    r = cli.post("/api/cartera/clear",
                 headers={**HDR, "Referer": "https://evil.example/page"})
    assert r.status_code == 403
    os.unlink(path)


def test_origin_alone_is_not_enough_without_the_header():
    """A same-origin request that forgot the header is still refused, so the
    two barriers are genuinely independent."""
    cli, path = _client()
    r = cli.post("/api/cartera/clear", headers={"Origin": "http://127.0.0.1:8771"})
    assert r.status_code == 403
    assert _count(path) == 1
    os.unlink(path)


def test_the_dashboard_itself_still_works():
    """The guard must not break the app it protects."""
    cli, path = _client()
    ok = {**HDR, "Origin": "http://127.0.0.1:8771"}
    r = cli.post("/api/cartera", headers={**ok, "Content-Type": "application/json"},
                 json={"ticker": "AAA", "quantity": 2, "price": 50})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _count(path) == 2
    r = cli.post("/api/cartera/clear", headers=ok)
    assert r.status_code == 200
    assert _count(path) == 0
    os.unlink(path)


def test_localhost_and_ipv6_origins_are_accepted():
    cli, path = _client()
    for origin in ("http://localhost:8771", "http://[::1]:8771"):
        r = cli.post("/api/cartera", headers={**HDR, "Origin": origin,
                                              "Content-Type": "application/json"},
                     json={"ticker": "BBB", "quantity": 1, "price": 10})
        assert r.status_code == 200, f"{origin} rejected"
    os.unlink(path)


def test_reads_are_untouched():
    """GET must stay open: the guard is for state changes only."""
    cli, path = _client()
    for url in ("/api/cartera", "/cartera", "/"):
        assert cli.get(url, headers={"Origin": EVIL}).status_code == 200
    os.unlink(path)


def test_curl_without_browser_headers_still_needs_the_header():
    """No Origin and no Referer means no browser context, so the origin check
    abstains — the header requirement must carry the defence on its own."""
    cli, path = _client()
    assert cli.post("/api/cartera/clear").status_code == 403
    assert _count(path) == 1
    assert cli.post("/api/cartera/clear", headers=HDR).status_code == 200
    os.unlink(path)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
