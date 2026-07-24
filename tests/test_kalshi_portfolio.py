"""Unit tests for the Kalshi portfolio fetchers. No network: a fake `fetch` yields
the two-page cursor responses, so pagination, tier-merge, series filtering, date
filtering, dedupe, and price/timestamp normalization are all exercised offline."""

from datetime import date, datetime, timezone

import sources.kalshi_portfolio as kp


def _fake_fills_fetch():
    """Returns a fetch(path, params) that pages /portfolio/fills (2 pages) and
    /historical/fills (1 page, with a duplicate trade_id to test dedupe)."""
    # Real Kalshi fill schema: count_fp (string, may be fractional) and
    # *_price_dollars (string, already in dollars). fill_id is the unique fill key.
    pages = {
        ("/portfolio/fills", None): {"fills": [
            {"fill_id": "t1", "trade_id": "t1", "ticker": "KXHIGHTDAL-26JUN22-B97",
             "side": "yes", "action": "buy", "count_fp": "10",
             "yes_price_dollars": "0.4200", "no_price_dollars": "0.5800",
             "created_time": "2026-06-22T19:47:00Z"},
        ], "cursor": "c2"},
        ("/portfolio/fills", "c2"): {"fills": [
            {"fill_id": "t2", "trade_id": "t2", "ticker": "KXLOWTDAL-26JUN22-B77",
             "side": "no", "action": "buy", "count_fp": "5",
             "yes_price_dollars": "0.3000", "no_price_dollars": "0.7000",
             "created_time": "2026-06-22T05:10:00Z"},
            {"fill_id": "t3", "ticker": "KXNOTDALLAS-26JUN22",  # off-series, dropped
             "side": "yes", "action": "buy", "count_fp": "1",
             "yes_price_dollars": "0.5000", "no_price_dollars": "0.5000",
             "created_time": "2026-06-22T12:00:00Z"},
            {"fill_id": "t4", "ticker": "KXHIGHTDAL-26JUN20-B95",  # before start, dropped
             "side": "yes", "action": "buy", "count_fp": "2",
             "yes_price_dollars": "0.2000", "no_price_dollars": "0.8000",
             "created_time": "2026-06-20T18:00:00Z"},
        ], "cursor": ""},
        ("/historical/fills", None): {"fills": [
            {"fill_id": "t1", "trade_id": "t1", "ticker": "KXHIGHTDAL-26JUN22-B97",  # dup, dropped
             "side": "yes", "action": "buy", "count_fp": "10",
             "yes_price_dollars": "0.4200", "no_price_dollars": "0.5800",
             "created_time": "2026-06-22T19:47:00Z"},
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
    assert t1["price"] == 0.42                        # yes buy -> yes_price_dollars
    assert t1["count"] == 10.0                        # count_fp string -> float
    assert t1["ts"] == datetime(2026, 6, 22, 19, 47, tzinfo=timezone.utc)
    t2 = next(f for f in out if f["trade_id"] == "t2")
    assert t2["price"] == 0.70                        # no buy -> no_price_dollars


def test_fills_cut_off_by_weather_day_not_utc_timestamp():
    """A bet placed the evening of the PRIOR weather day rolls into `start` in UTC
    but belongs to that earlier day (the page buckets by weather day). It must be
    dropped by ticker date, not kept by its timestamp; a genuine `start`-day market
    bought that same evening must be kept even though its timestamp is a day earlier."""
    pages = {
        ("/portfolio/fills", None): {"fills": [
            # Jul 22 market, bought 8pm CDT Jul 22 -> 01:10Z Jul 23. Timestamp is on
            # the Jul 23 start, but the weather day is Jul 22 -> DROP.
            {"fill_id": "prev", "trade_id": "prev", "ticker": "KXHIGHTDAL-26JUL22-B97",
             "side": "yes", "action": "buy", "count_fp": "3",
             "yes_price_dollars": "0.5000", "no_price_dollars": "0.5000",
             "created_time": "2026-07-23T01:10:00Z"},
            # Jul 23 market, bought 11pm CDT Jul 22 -> 04:00Z Jul 23. Weather day is
            # Jul 23 -> KEEP (even though someone might expect a Jul 22-ish timestamp).
            {"fill_id": "keep", "trade_id": "keep", "ticker": "KXHIGHTDAL-26JUL23-B97",
             "side": "yes", "action": "buy", "count_fp": "4",
             "yes_price_dollars": "0.5000", "no_price_dollars": "0.5000",
             "created_time": "2026-07-23T04:00:00Z"},
        ], "cursor": ""},
    }
    out = kp.fills(date(2026, 7, 23), fetch=lambda p, params=None: pages[(p, None)])
    assert sorted(f["trade_id"] for f in out) == ["keep"]


def test_settlements_keyed_by_ticker():
    def fetch(path, params=None):
        if path == "/portfolio/settlements":
            return {"settlements": [
                {"ticker": "KXHIGHTDAL-26JUN22-B97", "market_result": "yes",
                 "settled_time": "2026-06-23T06:00:00Z", "revenue": 1892}], "cursor": ""}
        return {"settlements": [], "cursor": ""}
    s = kp.settlements(date(2026, 6, 22), fetch=fetch)
    assert s["KXHIGHTDAL-26JUN22-B97"]["result"] == "yes"
    assert s["KXHIGHTDAL-26JUN22-B97"]["ts"] == datetime(2026, 6, 23, 6, 0, tzinfo=timezone.utc)
    assert s["KXHIGHTDAL-26JUN22-B97"]["revenue"] == 18.92     # 1892 cents -> dollars


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


def test_balance_cents_to_dollars():
    assert kp.balance(fetch=lambda path, params=None: {"balance": 3057}) == 30.57
    assert kp.balance(fetch=lambda path, params=None: {}) is None


def test_market_price_bid_of_held_side():
    m = {"market": {"yes_bid_dollars": "0.60", "yes_ask_dollars": "0.64",
                    "no_bid_dollars": "0.36", "no_ask_dollars": "0.40",
                    "last_price_dollars": "0.62"}}
    fp = lambda t: m
    assert kp.market_price("KXHIGHTDAL-26JUL07-B98.5", "yes", fetch_public=fp) == 0.60
    assert kp.market_price("KXHIGHTDAL-26JUL07-B98.5", "no", fetch_public=fp) == 0.36


def test_market_price_falls_back_to_last_when_no_bid():
    m = {"market": {"last_price_dollars": "0.62"}}   # no bid on either side
    fp = lambda t: m
    assert kp.market_price("T", "yes", fetch_public=fp) == 0.62
    assert kp.market_price("T", "no", fetch_public=fp) == 0.38   # 1 - 0.62


def test_market_price_none_when_no_prices():
    assert kp.market_price("T", "yes", fetch_public=lambda t: {"market": {}}) is None
