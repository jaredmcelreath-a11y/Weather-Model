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


def test_implied_forecast_reports_per_bucket_volume(monkeypatch):
    # Per-bracket traded volume is preserved for every PRICED contract, including
    # a low-priced tail that the PMF normalization trims out — so the settled
    # bracket's volume is always recoverable later even if its price was tiny.
    contracts = [
        {"strike_type": "between", "floor": 95, "cap": 96,
         "yes_bid": 0.40, "yes_ask": 0.42, "volume": 500.0},
        {"strike_type": "between", "floor": 97, "cap": 98,
         "yes_bid": 0.50, "yes_ask": 0.52, "volume": 800.0},
        {"strike_type": "between", "floor": 99, "cap": 100,   # price-trimmed tail
         "yes_bid": 0.00, "yes_ask": 0.01, "volume": 30.0},
    ]
    monkeypatch.setattr(kalshi, "fetch_contracts",
                        lambda v, d, s=config.DEFAULT_STATION: contracts)
    out = kalshi.implied_forecast("high", date(2026, 7, 27))
    assert out["bucket_volume"] == [[95, 96, 500.0], [97, 98, 800.0], [99, 100, 30.0]]
    # the trimmed tail is absent from the normalized PMF but present in volume
    assert [99, 100] not in [[f, c] for f, c, _ in out["buckets"]]


def test_implied_forecast_missing_volume_is_zero(monkeypatch):
    contracts = [{"strike_type": "between", "floor": 95, "cap": 96,
                  "yes_bid": 0.40, "yes_ask": 0.42, "volume": None}]
    monkeypatch.setattr(kalshi, "fetch_contracts",
                        lambda v, d, s=config.DEFAULT_STATION: contracts)
    out = kalshi.implied_forecast("high", date(2026, 7, 27))
    assert out["bucket_volume"] == [[95, 96, 0.0]]


def test_implied_block_routes_station(monkeypatch):
    seen = set()

    def fake_implied_forecast(variable, day, station=config.DEFAULT_STATION):
        seen.add(station)
        return None

    monkeypatch.setattr(kalshi, "implied_forecast", fake_implied_forecast)
    kalshi.implied_block(date(2026, 7, 27), date(2026, 7, 28), station="KAUS")
    assert seen == {"KAUS"}
