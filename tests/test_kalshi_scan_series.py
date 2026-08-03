from datetime import datetime, timedelta, timezone

from sources import kalshi

_NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)

_SERIES_PAYLOAD = {"series": [
    {"ticker": "KXHIGHDEN", "title": "Highest temperature in Denver"},
    {"ticker": "KXLOWTDEN", "title": "Lowest temperature in Denver"},
    {"ticker": "KXHIGHTEMPDEN", "title": "High temperature denver"},
    {"ticker": "KXHIGHUS", "title": "High temp in United States"},
    {"ticker": "KXHIGHNYD", "title": "Hourly Directional NYC Temperature"},
    {"ticker": "KXBTCD", "title": "Bitcoin price"},
]}


def test_discovery_keeps_city_high_low_series_only():
    got = kalshi.list_weather_series(fetch=lambda: _SERIES_PAYLOAD)
    assert [s["ticker"] for s in got] == [
        "KXHIGHDEN", "KXHIGHTEMPDEN", "KXLOWTDEN"]


def test_discovery_carries_the_title():
    got = kalshi.list_weather_series(fetch=lambda: _SERIES_PAYLOAD)
    assert got[0]["title"] == "Highest temperature in Denver"


def test_a_series_with_an_open_market_is_active():
    markets = [{"status": "active", "close_time": None}]
    assert kalshi.is_series_active(markets, _NOW) is True


def test_a_series_that_closed_inside_the_window_is_active():
    recent = (_NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    markets = [{"status": "finalized", "close_time": recent}]
    assert kalshi.is_series_active(markets, _NOW) is True


def test_a_long_dead_series_is_not_active():
    old = (_NOW - timedelta(days=90)).isoformat().replace("+00:00", "Z")
    markets = [{"status": "finalized", "close_time": old}]
    assert kalshi.is_series_active(markets, _NOW) is False


def test_no_markets_at_all_is_not_active():
    assert kalshi.is_series_active([], _NOW) is False


def test_list_series_markets_passes_the_series_and_status():
    seen = {}

    def fake(params):
        seen.update(params)
        return {"markets": [{"ticker": "KXHIGHDEN-26AUG03-B72.5"}]}

    got = kalshi.list_series_markets("KXHIGHDEN", status="settled", fetch=fake)
    assert seen["series_ticker"] == "KXHIGHDEN"
    assert seen["status"] == "settled"
    assert got[0]["ticker"] == "KXHIGHDEN-26AUG03-B72.5"


def test_parse_kalshi_ts_handles_the_z_suffix():
    got = kalshi.parse_kalshi_ts("2026-08-04T06:00:00Z")
    assert got == datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)


def test_parse_kalshi_ts_returns_none_for_junk():
    assert kalshi.parse_kalshi_ts(None) is None
    assert kalshi.parse_kalshi_ts("not a date") is None
