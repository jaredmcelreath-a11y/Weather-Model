"""Unit tests for the Kalshi portfolio fetchers. No network: a fake `fetch` yields
the two-page cursor responses, so pagination, tier-merge, series filtering, date
filtering, dedupe, and price/timestamp normalization are all exercised offline."""

from datetime import date, datetime, timezone

import sources.kalshi_portfolio as kp


def _fake_fills_fetch():
    """Returns a fetch(path, params) that pages /portfolio/fills (2 pages) and
    /historical/fills (1 page, with a duplicate trade_id to test dedupe)."""
    pages = {
        ("/portfolio/fills", None): {"fills": [
            {"trade_id": "t1", "ticker": "KXHIGHTDAL-26JUN22-B97",
             "side": "yes", "action": "buy", "count": 10,
             "yes_price": 42, "no_price": 58, "created_time": "2026-06-22T19:47:00Z"},
        ], "cursor": "c2"},
        ("/portfolio/fills", "c2"): {"fills": [
            {"trade_id": "t2", "ticker": "KXLOWTDAL-26JUN22-B77",
             "side": "no", "action": "buy", "count": 5,
             "yes_price": 30, "no_price": 70, "created_time": "2026-06-22T05:10:00Z"},
            {"trade_id": "t3", "ticker": "KXNOTDALLAS-26JUN22",  # off-series, dropped
             "side": "yes", "action": "buy", "count": 1,
             "yes_price": 50, "no_price": 50, "created_time": "2026-06-22T12:00:00Z"},
            {"trade_id": "t4", "ticker": "KXHIGHTDAL-26JUN20-B95",  # before start, dropped
             "side": "yes", "action": "buy", "count": 2,
             "yes_price": 20, "no_price": 80, "created_time": "2026-06-20T18:00:00Z"},
        ], "cursor": ""},
        ("/historical/fills", None): {"fills": [
            {"trade_id": "t1", "ticker": "KXHIGHTDAL-26JUN22-B97",  # dup of t1, dropped
             "side": "yes", "action": "buy", "count": 10,
             "yes_price": 42, "no_price": 58, "created_time": "2026-06-22T19:47:00Z"},
        ], "cursor": ""},
    }

    def fetch(path, params=None):
        cursor = (params or {}).get("cursor")
        return pages[(path, cursor)]
    return fetch


def test_fills_pages_merges_filters_and_dedupes():
    out = kp.fills(date(2026, 6, 22), fetch=_fake_fills_fetch())
    ids = sorted(f["trade_id"] for f in out)
    assert ids == ["t1", "t2"]                       # off-series/old/dup removed
    t1 = next(f for f in out if f["trade_id"] == "t1")
    assert t1["variable"] == "high"
    assert t1["price"] == 0.42                        # yes buy -> yes_price/100
    assert t1["ts"] == datetime(2026, 6, 22, 19, 47, tzinfo=timezone.utc)
    t2 = next(f for f in out if f["trade_id"] == "t2")
    assert t2["price"] == 0.70                        # no buy -> no_price/100


def test_settlements_keyed_by_ticker():
    def fetch(path, params=None):
        if path == "/portfolio/settlements":
            return {"settlements": [
                {"ticker": "KXHIGHTDAL-26JUN22-B97", "market_result": "yes",
                 "settled_time": "2026-06-23T06:00:00Z"}], "cursor": ""}
        return {"settlements": [], "cursor": ""}
    s = kp.settlements(date(2026, 6, 22), fetch=fetch)
    assert s["KXHIGHTDAL-26JUN22-B97"]["result"] == "yes"
    assert s["KXHIGHTDAL-26JUN22-B97"]["ts"] == datetime(2026, 6, 23, 6, 0, tzinfo=timezone.utc)


def test_market_meta_parses_public_market():
    def fetch_public(ticker):
        return {"market": {"ticker": ticker, "yes_sub_title": "97 to 98",
                           "floor_strike": 97, "cap_strike": 98,
                           "strike_type": "between"}}
    m = kp.market_meta("KXHIGHTDAL-26JUN22-B97", fetch_public=fetch_public)
    assert m == {"label": "97 to 98", "floor": 97, "cap": 98,
                 "strike_type": "between", "variable": "high"}


def test_variable_of():
    assert kp.variable_of("KXHIGHTDAL-26JUN22-B97") == "high"
    assert kp.variable_of("KXLOWTDAL-26JUN22-B77") == "low"
    assert kp.variable_of("KXOTHER-1") is None
