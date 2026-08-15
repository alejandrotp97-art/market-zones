"""Look-through geography: what the map is allowed to claim.

Every rule here exists so the map cannot show a number the portfolio does not
support — exposure invented from a position that could not be valued, weight
quietly redistributed to make a total reach 100%, or an instrument with no
country breakdown vanishing instead of being reported.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geo as G


def _pos(ticker, mval, valued=True, name=""):
    return {"ticker": ticker, "name": name or ticker, "qty": 1.0,
            "market_value": mval, "valued": valued,
            "why": None if valued else "sin precio"}


TABLE = {
    "as_of": "2026-08-06",
    "instruments": {
        "WORLD": {"name": "World fund", "asset_class": "equity", "no_geography": False,
                  "weights": {"US": 60.0, "JP": 20.0}, "other": 20.0,
                  "proxy": None, "source": "x", "as_of": "2026-08-06"},
        "BONDS": {"name": "Euro govt", "asset_class": "bond", "no_geography": False,
                  "weights": {"FR": 50.0, "DE": 50.0}, "other": 0.0,
                  "proxy": None, "source": "x", "as_of": "2026-08-06"},
        "GOLD": {"name": "Gold ETC", "asset_class": "commodity", "no_geography": True,
                 "weights": {}, "other": 0.0,
                 "proxy": None, "source": None, "as_of": "2026-08-06"},
    },
}


def _by_iso(res):
    return {c["iso2"]: c for c in res["countries"]}


# ── the arithmetic ────────────────────────────────────────────────────────
def test_weight_splits_market_value_across_countries():
    res = G.country_exposure([_pos("WORLD", 1000.0)], TABLE)
    c = _by_iso(res)
    assert c["US"]["eur"] == 600.0
    assert c["JP"]["eur"] == 200.0
    assert res["other_eur"] == 200.0          # the 20% justETF did not name


def test_two_instruments_touching_one_country_add_up():
    res = G.country_exposure(
        [_pos("WORLD", 1000.0), _pos("BONDS", 400.0)], TABLE)
    c = _by_iso(res)
    assert c["FR"]["eur"] == 200.0
    # France is reached only by the bond fund; the world fund never adds to it.
    assert [x["ticker"] for x in c["FR"]["contributors"]] == ["BONDS"]
    assert c["US"]["eur"] == 600.0


def test_percentages_are_of_the_mapped_total_not_the_portfolio():
    # 1000 in, 800 of it lands on a named country. US is 600/800, NOT 600/1000:
    # dividing by the portfolio would make every country look smaller than the
    # source says, and the columns would never agree with the fund's factsheet.
    res = G.country_exposure([_pos("WORLD", 1000.0)], TABLE)
    assert res["mapped_eur"] == 800.0
    assert _by_iso(res)["US"]["pct"] == 75.0
    assert round(sum(x["pct"] for x in res["countries"]), 6) == 100.0


# ── what must never contribute ────────────────────────────────────────────
def test_unvalued_position_contributes_nothing():
    # `market_value` is None when the position could not be valued. Treating
    # that as zero is fine; treating the position as present is not.
    res = G.country_exposure(
        [_pos("WORLD", 1000.0), _pos("BONDS", None, valued=False)], TABLE)
    assert _by_iso(res).get("FR") is None
    assert res["excluded"] == [{"ticker": "BONDS", "why": "sin precio"}]


def test_gold_never_paints_a_country():
    res = G.country_exposure([_pos("GOLD", 500.0)], TABLE)
    assert res["countries"] == []
    assert res["no_geography_eur"] == 500.0
    assert res["mapped_eur"] == 0.0


def test_instrument_missing_from_the_table_is_reported_not_dropped():
    res = G.country_exposure(
        [_pos("WORLD", 1000.0), _pos("MYSTERY", 500.0)], TABLE)
    assert res["unmapped"] == [{"ticker": "MYSTERY", "name": "MYSTERY",
                                "eur": 500.0, "why": "sin tabla de países"}]
    assert res["unmapped_eur"] == 500.0
    # and it did not dilute the countries that ARE known
    assert _by_iso(res)["US"]["pct"] == 75.0


# ── the asset-class filter ────────────────────────────────────────────────
def test_equity_filter_excludes_the_bond_fund():
    res = G.country_exposure(
        [_pos("WORLD", 1000.0), _pos("BONDS", 400.0)], TABLE, asset_class="equity")
    c = _by_iso(res)
    assert "FR" not in c and "DE" not in c
    assert c["US"]["eur"] == 600.0


def test_bond_filter_keeps_only_the_bond_fund():
    res = G.country_exposure(
        [_pos("WORLD", 1000.0), _pos("BONDS", 400.0)], TABLE, asset_class="bond")
    c = _by_iso(res)
    assert set(c) == {"FR", "DE"}
    assert c["FR"]["pct"] == 50.0


def test_filters_partition_the_mapped_total():
    positions = [_pos("WORLD", 1000.0), _pos("BONDS", 400.0)]
    everything = G.country_exposure(positions, TABLE)["mapped_eur"]
    parts = sum(G.country_exposure(positions, TABLE, asset_class=k)["mapped_eur"]
                for k in ("equity", "bond", "commodity"))
    assert round(parts, 6) == round(everything, 6)


# ── presentation invariants the map depends on ────────────────────────────
def test_countries_come_back_heaviest_first():
    res = G.country_exposure([_pos("WORLD", 1000.0), _pos("BONDS", 400.0)], TABLE)
    eurs = [c["eur"] for c in res["countries"]]
    assert eurs == sorted(eurs, reverse=True)


def test_contributors_name_the_funds_behind_a_country():
    res = G.country_exposure([_pos("WORLD", 1000.0, name="World fund")], TABLE)
    us = _by_iso(res)["US"]["contributors"]
    assert us == [{"ticker": "WORLD", "name": "World fund", "eur": 600.0}]


def test_empty_portfolio_is_empty_not_a_division_by_zero():
    res = G.country_exposure([], TABLE)
    assert res["countries"] == [] and res["mapped_eur"] == 0.0


def test_country_names_are_resolved_for_display():
    res = G.country_exposure([_pos("WORLD", 1000.0)], TABLE)
    assert _by_iso(res)["US"]["name"] == "Estados Unidos"


# ── a holding whose weights have not been resolved yet ────────────────────
def test_pending_reference_reads_differently_from_a_broken_table():
    # "Nobody has chosen a reference ETF yet" is a job someone can finish. An
    # empty table is something to go and look at. Reporting both as the same
    # sentence makes the fixable one invisible.
    table = {"as_of": "2026-08-06", "instruments": {
        **TABLE["instruments"],
        "PEND": {"name": "Fondo nuevo", "asset_class": "equity", "weights": {},
                 "other": 0.0, "no_geography": False, "needs_proxy": True},
        "BROKE": {"name": "Tabla vacía", "asset_class": "equity", "weights": {},
                  "other": 0.0, "no_geography": False, "needs_proxy": False}}}
    res = G.country_exposure([_pos("PEND", 100.0), _pos("BROKE", 50.0)], table)
    why = {u["ticker"]: u["why"] for u in res["unmapped"]}
    assert why["PEND"] == "pendiente de elegir ETF de referencia"
    assert why["BROKE"] == "tabla sin pesos"
    assert res["unmapped_eur"] == 150.0


# ── discovery: which instruments the seeder can resolve on its own ────────
import sys as _sys                                                    # noqa: E402
_sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analysis"))
import seed_country_weights as S                                      # noqa: E402


def test_listed_etf_resolves_to_itself_not_a_proxy():
    # The ISIN is in the ticker, so justETF profiles the real instrument.
    # Calling that a "proxy" would make the UI disclose an approximation that
    # is not happening.
    r = S.resolve("IE000M7V94E1.SG", "VanEck Uranium")
    assert r["proxy_isin"] == "IE000M7V94E1"
    assert r["is_proxy"] is False and r["needs_proxy"] is False


def test_hand_picked_proxy_is_marked_as_one():
    r = S.resolve("0P0001CLDK.F", "Fidelity MSCI World")
    assert r["proxy_isin"] == "IE00B4L5Y983"
    assert r["is_proxy"] is True


def test_gold_is_not_a_proxy_and_needs_nobody():
    r = S.resolve("FR0013416716.SG", "Amundi Physical Gold")
    assert r["proxy_isin"] is None
    assert r["is_proxy"] is False and r["needs_proxy"] is False


def test_unknown_fund_asks_for_a_human_choice_instead_of_guessing():
    # A search would happily return *an* ETF. The wrong share class of the
    # wrong index looks exactly as plausible on a map as the right one, so the
    # seeder refuses to pick.
    r = S.resolve("0P0000XYZ9.F", "Fondo que no conocemos")
    assert r["needs_proxy"] is True and r["proxy_isin"] is None
