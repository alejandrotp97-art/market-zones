"""Per-instance configuration: the two knobs that let ONE codebase serve
several people, each with their own portfolio file on their own port.

Isolation here is by FILE and PROCESS, not by a `WHERE user_id = ?`. That is
the whole point: there is no query to forget, so no request can reach another
person's book. These tests pin the two constants that carry that guarantee.

Both are read at import time, so every case runs in a FRESH interpreter —
reloading the module in-process would leak the patched environment into every
other test in the session.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _probe(code, **env):
    """Import dashboard in a clean interpreter under `env` and return stdout."""
    e = dict(os.environ)
    e.pop("CARTERA_DB", None)
    e.pop("MZ_PORT", None)
    e["PYTHONPATH"] = os.path.join(ROOT, "vendor")
    e.update({k: str(v) for k, v in env.items()})
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()[-1]


CONFIG = "import dashboard as D; import json; print(json.dumps([D.CARTERA_DB, D.PORT]))"


def test_defaults_are_exactly_todays_behaviour():
    # The running :8771 instance must not notice this change.
    db, port = json.loads(_probe(CONFIG))
    assert db == os.path.join(ROOT, "cartera.db")
    assert port == 8771


def test_portfolio_file_follows_the_environment():
    db, _ = json.loads(_probe(CONFIG, CARTERA_DB="/srv/carteras/ana.db"))
    assert db == "/srv/carteras/ana.db"


def test_port_follows_the_environment():
    _, port = json.loads(_probe(CONFIG, MZ_PORT=8782))
    assert port == 8782


def test_a_fresh_instance_creates_its_own_empty_book(tmp_path):
    """A new person starts from zero without anyone seeding a file for them:
    the first connection creates the schema, and creates it private (0600)."""
    target = tmp_path / "nueva.db"
    code = (
        "import dashboard as D, os, json;"
        " c = D._cartera_conn();"
        " n = c.execute('select count(*) from movements').fetchone()[0];"
        " print(json.dumps([os.path.exists(D.CARTERA_DB), n,"
        " oct(os.stat(D.CARTERA_DB).st_mode & 0o777)]))"
    )
    exists, rows, mode = json.loads(_probe(code, CARTERA_DB=str(target)))
    assert exists is True
    assert rows == 0                      # empty book, not a copy of anyone else's
    assert mode == "0o600"                # not readable by other local accounts
