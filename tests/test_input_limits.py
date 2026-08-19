"""V3 (SSRF) and V4 (unbounded upload): the two places where a caller's input
decides how much work — and which network requests — the server performs.

Everything here runs offline or against a throwaway database.
"""
import io
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D
from zones import BadSymbol, safe_symbol

HDR = {D.CSRF_HEADER: "1", "Host": "127.0.0.1:8771"}


# ── V3 · the symbol must not steer the URL ────────────────────────────────
def test_real_tickers_survive():
    """Every shape actually in use, including the ones needing escapes."""
    assert safe_symbol("NLR") == "NLR"
    assert safe_symbol("BTC-USD") == "BTC-USD"
    assert safe_symbol("0P0001CLDK.F") == "0P0001CLDK.F"
    assert safe_symbol("IE000M7V94E1.SG") == "IE000M7V94E1.SG"
    assert safe_symbol("^RUT") == "%5ERUT"
    assert safe_symbol("BZ=F") == "BZ%3DF"
    assert safe_symbol("EURUSD=X") == "EURUSD%3DX"
    assert safe_symbol("  SPY  ") == "SPY"


def test_path_traversal_is_refused():
    """The verified attack: `..%2F..%2F..%2Fv1/test/getcrumb` walked out of the
    chart endpoint and reached a different Yahoo API.

    `..` alone is the subtle one: `.` must stay in the charset for symbols like
    `0P0001CLDK.F`, so a character-set rule by itself accepts it — and a lone
    `..` is still one segment that walks up a level."""
    for bad in ("../../../v1/test/getcrumb", "..", ".", "...", "./x", "a/b",
                "%2e%2e%2f", "..%2F"):
        try:
            safe_symbol(bad)
            raise AssertionError(f"{bad!r} was accepted")
        except BadSymbol:
            pass


def test_query_and_fragment_injection_is_refused():
    """`SPY?period1=0&x=` appended our own parameters; `SPY#` truncated them."""
    for bad in ("SPY?period1=0", "SPY&x=1", "SPY#", "SPY%20", "SP Y",
                "SPY\nX", "SPY\r\nHost: x", "SPY\x00"):
        try:
            safe_symbol(bad)
            raise AssertionError(f"{bad!r} was accepted")
        except BadSymbol:
            pass


def test_surrounding_whitespace_is_trimmed_but_embedded_is_not():
    """Trimming is a deliberate convenience — tickers get pasted with spaces.
    Whitespace INSIDE the symbol is a different thing and stays rejected."""
    assert safe_symbol("  ^RUT \n") == "%5ERUT"
    for bad in ("^R UT", "A\tB", "A\nB"):
        try:
            safe_symbol(bad)
            raise AssertionError(f"{bad!r} was accepted")
        except BadSymbol:
            pass


def test_empty_and_oversized_are_refused():
    for bad in ("", "   ", None, "A" * 33):
        try:
            safe_symbol(bad)
            raise AssertionError(f"{bad!r} was accepted")
        except BadSymbol:
            pass
    assert safe_symbol("A" * 32) == "A" * 32     # the boundary itself is fine


def test_api_answers_400_not_502_for_a_bad_symbol():
    """A malformed ticker is the caller's mistake, not an upstream failure —
    and it must never reach the network to find that out."""
    D.app.config["TESTING"] = True
    cli = D.app.test_client()
    r = cli.get("/api/zones?symbol=../../../v1/test/getcrumb")
    assert r.status_code == 400
    assert "no válido" in r.get_json()["error"]


# ── V4 · the upload must not decide how long the server works ─────────────
def _client():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    D.CARTERA_DB = path
    with D._cartera_conn() as c:
        c.execute("DELETE FROM movements")
    D.app.config["TESTING"] = True
    return D.app.test_client(), path


def _csv(n, sep=","):
    head = sep.join(["Fecha", "Ticker", "Tipo", "Cantidad", "Precio"])
    row = sep.join(["01/01/2024", "AAA", "Compra", "1", "1"])
    return (head + "\n" + (row + "\n") * n).encode()


def test_oversized_body_is_rejected_before_it_is_read():
    cli, path = _client()
    big = b"x" * (D.MAX_UPLOAD_MB * 1024 * 1024 + 1024)
    r = cli.post("/api/cartera/upload", headers=HDR,
                 data={"file": (io.BytesIO(big), "big.csv")},
                 content_type="multipart/form-data")
    assert r.status_code == 413
    assert "MB" in r.get_json()["error"]
    os.unlink(path)


def test_row_cap_bounds_the_python_loop():
    """The real cost is ~150 us per row, so the row count — not the byte count —
    is what decides how long a worker is held."""
    rows, errors, _ = D._parse_upload("x.csv", _csv(D.MAX_UPLOAD_ROWS + 500))
    assert len(rows) == D.MAX_UPLOAD_ROWS
    assert any("excede" in e for e in errors), "truncation must be reported"


def test_a_hostile_file_within_the_size_limit_is_still_fast():
    """A 4 MB CSV is ~150k rows; uncapped that was minutes of work."""
    data = _csv(150000)
    assert len(data) < D.MAX_UPLOAD_MB * 1024 * 1024, "fixture must fit the size cap"
    t0 = time.perf_counter()
    rows, _, _ = D._parse_upload("x.csv", data)
    dt = time.perf_counter() - t0
    assert len(rows) == D.MAX_UPLOAD_ROWS
    assert dt < 5.0, f"took {dt:.1f}s — the cap is not bounding the work"


def test_isin_lookups_are_capped_and_memoised():
    """Each distinct ISIN is one outbound Yahoo search: a crafted file would be
    a request amplifier aimed at a third party."""
    calls = []
    orig = D._resolve_symbol
    D._resolve_symbol = lambda q: (calls.append(q), ("", "", ""))[1]
    try:
        head = "Fecha,Ticker,Tipo,Cantidad,Precio\n"
        body = "".join(f"01/01/2024,ES{i:010d},Compra,1,1\n" for i in range(300))
        D._parse_upload("x.csv", (head + body).encode())
        assert len(calls) <= D.MAX_ISIN_LOOKUPS, f"{len(calls)} outbound lookups"
        # and a repeated ISIN must not pay twice
        calls.clear()
        rep = "".join("01/01/2024,ES0000000001,Compra,1,1\n" for _ in range(200))
        D._parse_upload("x.csv", (head + rep).encode())
        assert len(calls) == 1, f"{len(calls)} lookups for one distinct ISIN"
    finally:
        D._resolve_symbol = orig


def test_separator_sniffing_still_handles_real_exports():
    for sep in (",", ";", "\t", "|"):
        rows, _, detected = D._parse_upload("x.csv", _csv(3, sep))
        assert len(rows) == 3, f"separator {sep!r} broke parsing"
        assert {"date", "ticker", "side", "quantity", "price"} <= set(detected)


def test_normal_import_is_unaffected():
    cli, path = _client()
    r = cli.post("/api/cartera/upload", headers=HDR,
                 data={"file": (io.BytesIO(_csv(5, ";")), "ok.csv")},
                 content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["import"]["added"] == 5
    os.unlink(path)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")
