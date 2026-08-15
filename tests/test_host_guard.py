"""Host validation — the barrier against DNS rebinding.

Listening on 127.0.0.1 keeps the network out. It does not keep out a NAME that
resolves to 127.0.0.1: the attacker serves a page from evil.example with a
one-second TTL, re-points the record at loopback, and their script fetches
`http://evil.example:8771/api/cartera`. The browser considers that same-origin,
so CORS never applies and the response is readable. Reads are the whole point of
the attack, so unlike the CSRF gate this check must cover GET too.

The one header the attacker cannot launder is `Host` — it still names their
domain.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D

READS = ["/", "/cartera", "/comite", "/screener", "/api/cartera"]


def _client():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    D.CARTERA_DB = path
    with D._cartera_conn() as c:
        c.execute("DELETE FROM movements")
    D.app.config["TESTING"] = True
    return D.app.test_client(), path


def test_rebound_host_cannot_read_anything():
    cli, path = _client()
    for url in READS:
        r = cli.get(url, headers={"Host": "evil.example:8771"})
        assert r.status_code == 400, f"{url} answered a rebound host ({r.status_code})"
    os.unlink(path)


def test_names_that_merely_look_like_loopback_are_rejected():
    """`127.0.0.1.nip.io` and friends resolve to loopback BY DESIGN and are the
    standard shortcut for a rebinding proof-of-concept — no DNS trickery needed.
    Matching must be on the whole hostname, never a prefix."""
    cli, path = _client()
    for host in ("127.0.0.1.nip.io", "127.0.0.1evil.com", "localhost.evil.com",
                 "evil.com", "127.0.0.1.evil.com"):
        r = cli.get("/api/cartera", headers={"Host": host})
        assert r.status_code == 400, f"{host} was accepted"
    os.unlink(path)


def test_legitimate_local_hosts_still_work():
    cli, path = _client()
    for host in ("127.0.0.1:8771", "127.0.0.1", "localhost:8771", "localhost"):
        r = cli.get("/api/cartera", headers={"Host": host})
        assert r.status_code == 200, f"{host} was rejected ({r.status_code})"
    os.unlink(path)


def test_any_local_port_works_so_the_ssh_tunnel_is_free():
    """`ssh -L 9000:127.0.0.1:8771` makes the browser send Host: 127.0.0.1:9000.
    The port must not be part of the decision."""
    cli, path = _client()
    for host in ("127.0.0.1:9000", "localhost:1234", "127.0.0.1:65535"):
        assert cli.get("/api/cartera", headers={"Host": host}).status_code == 200
    os.unlink(path)


def test_a_rebound_write_is_refused_by_whichever_gate_sees_it_first():
    """The two checks are independent and BOTH are enforced; which one reports
    depends on the order Flask happens to run them in, so assert the outcome,
    not the status code.

    `before_request` runs ahead of Werkzeug's host validation, so a rebound POST
    that also fails CSRF gets 403 from this app; one that presents perfect CSRF
    credentials falls through to the host check and gets 400. Either way it does
    not write.
    """
    cli, path = _client()
    import sqlite3

    def count():
        c = sqlite3.connect(path)
        try:
            return c.execute("SELECT COUNT(*) FROM movements").fetchone()[0]
        finally:
            c.close()

    with D._cartera_conn() as c:
        c.execute("INSERT INTO movements(date,ticker,side,quantity,price,fee)"
                  " VALUES('2024-01-01','T','buy',1,1,0)")
    rebound = {"Host": "evil.example:8771", "Origin": "http://evil.example:8771"}
    # what a rebound browser can actually send: same-origin, so it CAN set the header
    assert cli.post("/api/cartera/clear", headers={**rebound, D.CSRF_HEADER: "1"}
                    ).status_code == 403
    # and the host check still stands on its own if the origin were ever spoofed
    assert cli.post("/api/cartera/clear",
                    headers={D.CSRF_HEADER: "1", "Host": "evil.example:8771",
                             "Origin": "http://127.0.0.1:8771"}).status_code == 400
    assert count() == 1, "a rebound request wrote to the database"
    os.unlink(path)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
