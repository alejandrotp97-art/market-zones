"""Portfolio bookkeeping: the rules that decide what a number on screen means.

Every test here corresponds to a defect that produced a WRONG NUMBER rather
than an error — the expensive kind. The network is stubbed out so the arithmetic
is the only thing under test.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as D


def _stub(ccy="EUR", fx=1.0, price=100.0):
    """Pin the market so only the bookkeeping varies."""
    D._instrument_ccy = lambda s: ccy
    D._fx_series_eur = lambda c: None
    D._fx_now = lambda c: fx
    D._last_price = lambda t: price


def _mov(i, side, q, px, ticker="AAA", date="2024-01-01", fee=0.0):
    return {"id": i, "date": date, "ticker": ticker, "side": side, "quantity": q,
            "price": px, "fee": fee, "name": "", "kind": ""}


# ── side normalization ────────────────────────────────────────────────────
def test_fund_vocabulary_is_not_inverted():
    # "Suscripción" is a PURCHASE of fund units and "Reembolso" a redemption.
    # Prefix matching used to read the leading "s" and "r" and swap both.
    assert D._norm_side("Suscripción", 10) == "buy"
    assert D._norm_side("Suscripcion", 10) == "buy"
    assert D._norm_side("Reembolso", 10) == "sell"
    assert D._norm_side("Aportación", 10) == "buy"
    assert D._norm_side("Rescate", 10) == "sell"


def test_plain_vocabulary_still_works():
    for v in ("Compra", "compra a mercado", "BUY", "c", "+"):
        assert D._norm_side(v, 10) == "buy", v
    for v in ("Venta", "VENTA PARCIAL", "sell", "v", "-"):
        assert D._norm_side(v, 10) == "sell", v


def test_unknown_word_falls_back_to_sign_not_to_first_letter():
    assert D._norm_side("Sarasa", 10) == "buy"      # positive qty
    assert D._norm_side("Sarasa", -10) == "sell"    # negative qty
    assert D._norm_side("", 5) == "buy"


def test_single_char_codes_only_count_as_whole_values():
    """As substrings these are everywhere: the hyphen inside a date, the "s" of
    any word. Each false hit inverts a trade, so they must match exactly."""
    assert D._norm_side("2024-01-01", 10) == "buy", "hyphens in a date are not a sale"
    assert D._norm_side("orden ejecutada", 10) == "buy"
    assert D._norm_side("-", 10) == "sell"          # but alone it still means sale
    assert D._norm_side("S", 10) == "sell"


def test_realized_is_withheld_when_fx_is_missing():
    """Realized P&L is a EUR number; accumulated across an fx gap it is partial,
    and a partial total published as complete is the whole disease."""
    _stub(ccy="USD", fx=None, price=110.0)
    p = D._positions([_mov(1, "buy", 10, 100), _mov(2, "sell", 4, 120, date="2024-06-01")])[0]
    assert p["realized"] is None


# ── overselling ───────────────────────────────────────────────────────────
def test_oversell_is_clamped_and_flagged():
    _stub()
    p = D._positions([_mov(1, "buy", 10, 100), _mov(2, "sell", 25, 120, date="2024-06-01")])[0]
    assert p["qty"] == 0.0, "a sale cannot drive the position negative"
    assert p["oversold"] == 15.0, "the excess must be reported, not absorbed"


def test_sell_without_prior_buy_does_not_invent_shares():
    _stub()
    p = D._positions([_mov(1, "sell", 5, 50)])[0]
    assert p["qty"] == 0.0 and p["oversold"] == 5.0
    assert p["realized"] == 0.0


def test_normal_partial_sale_still_realizes_pnl():
    _stub(price=130.0)
    p = D._positions([_mov(1, "buy", 10, 100), _mov(2, "sell", 4, 120, date="2024-06-01")])[0]
    assert abs(p["qty"] - 6.0) < 1e-9
    assert abs(p["realized"] - 80.0) < 1e-6      # 4 * (120 - 100)
    assert p["oversold"] == 0.0


# ── currency honesty ──────────────────────────────────────────────────────
def test_unknown_fx_refuses_to_value_instead_of_assuming_parity():
    _stub(ccy="USD", fx=None, price=110.0)
    p = D._positions([_mov(1, "buy", 10, 100)])[0]
    assert p["market_value"] is None and p["invested"] is None
    assert p["valued"] is False and p["why"] == "sin tipo de cambio"
    assert p["fx"] is None, "1.0 here prices a dollar as a euro"


def test_unknown_currency_refuses_to_value():
    _stub(ccy=None, price=110.0)
    p = D._positions([_mov(1, "buy", 10, 100)])[0]
    assert p["valued"] is False and p["why"] == "moneda desconocida"


def test_known_fx_converts():
    _stub(ccy="USD", fx=0.90, price=110.0)
    p = D._positions([_mov(1, "buy", 10, 100)])[0]
    assert p["valued"] is True
    assert abs(p["invested"] - 900.0) < 1e-6      # 10*100 * 0.90
    assert abs(p["market_value"] - 990.0) < 1e-6  # 10*110 * 0.90


# ── summary consistency ───────────────────────────────────────────────────
def test_summary_sums_a_single_consistent_set():
    """invested / market_value / unreal must cover the SAME positions. Mixing a
    partial numerator with a full denominator reads on screen as a loss."""
    calls = {}

    def ccy(sym):
        return None if sym == "BBB" else "EUR"    # BBB cannot be valued
    D._instrument_ccy = ccy
    D._fx_series_eur = lambda c: None
    D._fx_now = lambda c: 1.0
    D._last_price = lambda t: 110.0
    calls.clear()

    pos = D._positions([_mov(1, "buy", 10, 100, ticker="AAA"),
                        _mov(2, "buy", 10, 100, ticker="BBB")])
    valued = [p for p in pos if p["valued"]]
    assert len(valued) == 1
    inv = sum(p["invested"] for p in valued)
    mv = sum(p["market_value"] for p in valued)
    un = sum(p["unreal"] for p in valued)
    assert abs(inv - 1000.0) < 1e-6 and abs(mv - 1100.0) < 1e-6
    assert abs(un - (mv - inv)) < 1e-6, "unreal must reconcile with its own totals"


# ── movement ordering ─────────────────────────────────────────────────────
def test_undated_movement_sorts_last_not_first():
    """An empty date sorts before every real date, so an unparsed date used to
    become the OLDEST movement and silently reset the average cost."""
    _stub(price=100.0)
    movs = [_mov(1, "buy", 10, 100, date="2024-01-01"),
            _mov(2, "buy", 10, 300, date="")]      # undated, entered later
    p = D._positions(movs)[0]
    assert abs(p["avg_cost"] - 200.0) < 1e-6       # both counted, order irrelevant here
    # the ordering itself is what matters once a sale is involved:
    movs2 = [_mov(1, "buy", 10, 100, date="2024-01-01"),
             _mov(2, "sell", 5, 150, date="2024-06-01"),
             _mov(3, "buy", 5, 400, date="")]      # undated must NOT precede the sale
    p2 = D._positions(movs2)[0]
    assert abs(p2["realized"] - 250.0) < 1e-6, "sale priced against the 100 lot, not the 400"


# ── duplicate detection ───────────────────────────────────────────────────
def test_mov_key_identifies_exact_duplicates():
    a = {"date": "2024-06-04", "ticker": "aaa", "side": "buy", "quantity": 0.06, "price": 193.1667}
    b = {"date": "2024-06-04", "ticker": "AAA", "side": "buy", "quantity": 0.06, "price": 193.1667}
    c = {"date": "2024-06-05", "ticker": "AAA", "side": "buy", "quantity": 0.06, "price": 193.1667}
    assert D._mov_key(a) == D._mov_key(b)          # ticker case must not matter
    assert D._mov_key(a) != D._mov_key(c)


# ── cache hygiene ─────────────────────────────────────────────────────────
def test_cache_put_evicts_oldest():
    cache = {}
    for i in range(10):
        D._cache_put(cache, f"k{i}", i, cap=4)
    assert len(cache) == 4
    assert "k0" not in cache and "k9" in cache


def test_payload_cache_is_bounded_by_bytes_not_entries():
    """Entries differ by ~50x, so a count cap bounds the wrong quantity: it
    allowed hundreds of MB of large payloads while evicting a warmed set of
    small ones. Budget the bytes."""
    D._cache.clear()
    D._cache_bytes = 0
    big = b"x" * (1024 * 1024)
    small = b"y" * 1024
    for i in range(60):                       # 60 MB of large payloads
        D._payload_put(f"big{i}", big, b"")
    assert D._cache_bytes <= D.CACHE_BUDGET
    assert sum(len(v[1]) + len(v[2]) for v in D._cache.values()) == D._cache_bytes
    n_big = len(D._cache)
    D._cache.clear()
    D._cache_bytes = 0
    for i in range(2000):                     # 2 MB of small ones
        D._payload_put(f"s{i}", small, b"")
    assert len(D._cache) == 2000, "small entries must not be evicted by a count cap"
    assert n_big < 2000, "large entries must be evicted sooner than small ones"
    D._cache.clear()
    D._cache_bytes = 0


def test_payload_cache_accounting_survives_overwrite():
    D._cache.clear()
    D._cache_bytes = 0
    D._payload_put("k", b"a" * 100, b"b" * 50)
    D._payload_put("k", b"a" * 10, b"b" * 5)   # same key, smaller payload
    assert len(D._cache) == 1
    assert D._cache_bytes == 15, "a re-insert must not double-count"
    D._cache.clear()
    D._cache_bytes = 0


# ── instrument type ───────────────────────────────────────────────────────
class _FakeResp:
    """Minimal stand-in for a urlopen response: json.load() only calls read()."""
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_listed_line_is_never_labelled_an_unlisted_fund():
    """Yahoo answers MUTUALFUND for IE000M7V94E1.SG and FR0013416716.SG. Both are
    exchange lines, and an unlisted fund has no line on any exchange. The first
    had already been imported 31 times as ETF — one holding, two badges."""
    assert D._instrument_kind("IE000M7V94E1.SG", "Fondo") == "ETF"
    assert D._instrument_kind("FR0013416716.SG", "Fondo") == "ETF"


def test_a_type_we_were_given_is_not_overwritten():
    assert D._instrument_kind("FR0013416716.SG", "ETC") == "ETC"     # the user's own word
    assert D._instrument_kind("IE000M7V94E1.SG", "ETF") == "ETF"
    assert D._instrument_kind("IE000M7V94E1.SG", "Acción") == "Acción"


def test_an_unknown_type_stays_unknown():
    """Only the contradiction is overruled. Calling an unlabelled listing "ETF"
    would be a nicer-looking wrong answer for a bond or an ETC, not a better one."""
    assert D._instrument_kind("IE000M7V94E1.SG", "") == ""
    assert D._instrument_kind("NUKL.DE", "") == ""
    assert D._instrument_kind("", "") == ""


def test_yahoo_fund_symbols_are_funds():
    """`0P...` is Yahoo's synthetic symbol for something quoted at NAV, which is
    the one case where "Fondo" is certain."""
    assert D._instrument_kind("0P0001CLDK.F", "") == "Fondo"
    assert D._instrument_kind("0P0001CLDK.F", "ETF") == "Fondo"


def test_a_bare_isin_concludes_nothing():
    """With no exchange suffix the ISIN was never resolved to a listing (the
    lookup failed or was capped), so there is nothing to read from it."""
    assert D._instrument_kind("IE000M7V94E1", "Fondo") == "Fondo"
    assert D._symbol_isin("IE000M7V94E1.SG") == "IE000M7V94E1"
    assert D._symbol_isin("NUKL.DE") == ""
    assert D._symbol_isin("") == ""


def test_search_shows_the_corrected_type_and_the_isin_own_listing():
    """Both halves of the defect live here: the dropdown is where the wrong label
    was PICKED, and preferring a sibling by type would have stored the holding
    under a second ticker — NUCL.SW is another currency and another share class."""
    import urllib.request as U
    real = U.urlopen
    U.urlopen = lambda req, timeout=0: _FakeResp({"quotes": [
        {"symbol": "IE000M7V94E1.SG", "quoteType": "MUTUALFUND", "exchDisp": "Stuttgart",
         "shortname": "VanEck Uranium and Nuclear Tech"},
        {"symbol": "NUCL.SW", "quoteType": "ETF", "exchDisp": "Suiza",
         "shortname": "VanEck Nuclear UCITS ETF"}]})
    try:
        D._search_cache.clear()
        res = D._yahoo_search("IE000M7V94E1")
        assert res[0]["kind"] == "ETF", "the dropdown must not offer 'Fondo' to click"
        D._search_cache.clear()
        sym, name, kind = D._resolve_symbol("IE000M7V94E1")
    finally:
        U.urlopen = real
        D._search_cache.clear()
    assert sym == "IE000M7V94E1.SG", "the ISIN's own listing, not a sibling share class"
    assert kind == "ETF"


def test_stored_rows_cannot_disagree_about_one_ticker():
    """The reported symptom: two hand-added movements arrived as "Fondo" while 31
    imported ones said "ETF" — same ISIN, same line. `_positions` takes the type
    from the most recent movement, so the newest label re-badged the position."""
    _stub()
    db = tempfile.mkdtemp() + "/cartera.db"
    real_db = D.CARTERA_DB
    D.CARTERA_DB = db
    try:
        with D._cartera_conn() as c:
            c.executemany(
                "INSERT INTO movements(date,ticker,name,kind,side,quantity,price,fee) "
                "VALUES(?,?,?,?,?,?,?,0)",
                [("2026-08-03", "IE000M7V94E1.SG", "VanEck", "ETF", "buy", 1.0, 44.61),
                 ("2026-08-10", "IE000M7V94E1.SG", "VanEck", "Fondo", "buy", 1.0, 47.17)])
        payload = D._cartera_payload()
    finally:
        D.CARTERA_DB = real_db
    assert {m["kind"] for m in payload["movements"]} == {"ETF"}
    assert payload["positions"][0]["kind"] == "ETF"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"\n{len(fns)} passed")


# ── proxy de la página de swing ───────────────────────────────────────────
def test_swing_reports_the_outage_instead_of_serving_a_blank_page():
    """zonas-v2 corre en otro servicio. Si no responde, una página vacía se
    leería como «no hay datos» — una afirmación sobre el mercado— cuando la
    verdad es «no hay servicio», una afirmación sobre la máquina."""
    real = D.ZONAS_V2_URL
    D.ZONAS_V2_URL = "http://127.0.0.1:9"          # discard: nadie escucha
    try:
        r = D.app.test_client().get("/swing")
    finally:
        D.ZONAS_V2_URL = real
    assert r.status_code == 503
    assert "no disponible" in r.data.decode().lower()


def test_swing_forwards_the_upstream_body_untouched():
    import urllib.request as U
    real_open, real_url = U.urlopen, D.ZONAS_V2_URL
    payload = b"<!doctype html><html><body>pagina de swing</body></html>"

    class _Resp:
        def read(self, *a):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    U.urlopen = lambda req, timeout=0: _Resp()
    try:
        r = D.app.test_client().get("/swing")
    finally:
        U.urlopen, D.ZONAS_V2_URL = real_open, real_url
    assert r.status_code == 200 and r.data == payload
