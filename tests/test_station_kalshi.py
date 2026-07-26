from datetime import date

import config
from sources import kalshi


def test_series_for_by_station():
    assert kalshi.series_for("high") == config.station("KDFW").kalshi_high_series
    assert kalshi.series_for("high") == "KXHIGHTDAL"
    assert kalshi.series_for("low", "KAUS") == "KXLOWTAUS"
    assert kalshi.series_for("high", "KAUS") == "KXHIGHAUS"
    assert kalshi.series_for("bogus") is None


def test_fetch_contracts_uses_station_series(monkeypatch):
    seen = {}

    def fake_get_json(url, params, ttl=None):
        seen["series"] = params["series_ticker"]
        return {"markets": []}

    monkeypatch.setattr(kalshi, "get_json", fake_get_json)
    kalshi.fetch_contracts("high", date(2026, 7, 27), station="KAUS")
    assert seen["series"] == "KXHIGHAUS"
    kalshi.fetch_contracts("low", date(2026, 7, 27))  # KDFW default
    assert seen["series"] == "KXLOWTDAL"


def test_implied_block_routes_station(monkeypatch):
    seen = set()

    def fake_implied_forecast(variable, day, station=config.DEFAULT_STATION):
        seen.add(station)
        return None

    monkeypatch.setattr(kalshi, "implied_forecast", fake_implied_forecast)
    kalshi.implied_block(date(2026, 7, 27), date(2026, 7, 28), station="KAUS")
    assert seen == {"KAUS"}
