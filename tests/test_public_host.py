"""Serving this instance under a NAME instead of loopback.

Two guards in this service were calibrated for "the only way in is an SSH
tunnel", and both reject a proxied request outright:

  * TRUSTED_HOSTS — Werkzeug answers 400 before any view runs, so every page
    breaks, reads included.
  * the CSRF origin check — a browser on the public site sends that site as
    Origin, which is not loopback, so every write is refused.

Neither is removed here. Both are widened by exactly ONE name, the one this
instance is actually served under, and they keep rejecting every other name —
which is what makes them worth having. With the variable unset, behaviour is
bit-for-bit what it was.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = "cartera.ejemplo.com"


def _config_under(env=None):
    e = dict(os.environ)
    e.pop("PUBLIC_HOST", None)
    e["PYTHONPATH"] = os.path.join(ROOT, "vendor")
    e.update(env or {})
    code = ("import dashboard as D, json;"
            " print(json.dumps([sorted(D.app.config['TRUSTED_HOSTS']),"
            " sorted(D.ALLOWED_ORIGIN_HOSTS)]))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ── configuration ────────────────────────────────────────────────────────────

def test_unset_leaves_both_guards_exactly_as_they_were():
    trusted, origins = _config_under()
    assert trusted == ["127.0.0.1", "localhost"]
    assert origins == sorted({"127.0.0.1", "localhost", "::1", "[::1]"})


def test_public_host_widens_both_guards_by_one_name():
    trusted, origins = _config_under({"PUBLIC_HOST": PUBLIC})
    assert PUBLIC in trusted and "127.0.0.1" in trusted      # tunnel still works
    assert PUBLIC in origins and "127.0.0.1" in origins


# ── behaviour ────────────────────────────────────────────────────────────────

def _client():
    D.app.config["TESTING"] = True
    return D.app.test_client()


def test_the_public_name_is_served_and_every_other_name_is_not(monkeypatch):
    monkeypatch.setitem(D.app.config, "TRUSTED_HOSTS", ["127.0.0.1", PUBLIC])
    cli = _client()
    assert cli.get("/cartera", headers={"Host": PUBLIC}).status_code == 200
    # A rebinding attacker's name still cannot read a single byte.
    assert cli.get("/cartera", headers={"Host": "evil.example"}).status_code == 400


def test_writes_are_accepted_from_the_public_site_and_refused_from_anywhere_else(monkeypatch):
    monkeypatch.setattr(D, "ALLOWED_ORIGIN_HOSTS", {"127.0.0.1", PUBLIC})
    cli = _client()
    hdr = {"X-Market-Zones": "1"}                 # the non-safelisted-header barrier

    ok = cli.post("/api/cartera", json={}, headers={**hdr, "Origin": f"https://{PUBLIC}"})
    assert ok.status_code != 403                  # passes the origin gate

    blocked = cli.post("/api/cartera", json={},
                       headers={**hdr, "Origin": "https://evil.example"})
    assert blocked.status_code == 403
