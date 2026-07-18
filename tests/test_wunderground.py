"""Wunderground / TWC hourly-forecast + PWS adapter — parsing and shape."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
from sources import wunderground

_TZ = ZoneInfo(TIMEZONE)

# 1784350800 == 2026-07-18T00:00:00-05:00 (America/Chicago, CDT)
_HOURLY = {
    "validTimeUtc": [1784350800, 1784354400, 1784358000],
    "temperature": [84, 83, 82],
    "temperatureFeelsLike": [89, 88, 87],
    "temperatureDewPoint": [73, 72, 71],
    "precipChance": [1, 5, 10],
    "cloudCover": [33, 32, 30],
    "relativeHumidity": [62, 65, 68],
    "windSpeed": [11, 12, 13],
    "windDirectionCardinal": ["S", "SSW", "SW"],
}


def test_hourly_parses_parallel_arrays_into_per_hour_dicts(monkeypatch):
    monkeypatch.setattr(wunderground, "get_json", lambda url, params, **kw: _HOURLY)
    rows = wunderground.hourly()
    assert len(rows) == 3
    first = rows[0]
    assert first["temp"] == 84
    assert first["feels"] == 89
    assert first["dew"] == 73
    assert first["precip_pct"] == 1
    assert first["cloud_pct"] == 33
    assert first["humidity"] == 62
    assert first["wind_mph"] == 11
    assert first["wind_dir"] == "S"
    # time is tz-aware local; the first row is local midnight
    assert first["time"].tzinfo is not None
    assert first["time"].astimezone(_TZ).hour == 0
    assert rows[2]["wind_dir"] == "SW"


def test_hourly_requests_kdfw_geocode_imperial(monkeypatch):
    seen = {}

    def fake_get_json(url, params, **kw):
        seen["url"] = url
        seen["params"] = params
        return _HOURLY

    monkeypatch.setattr(wunderground, "get_json", fake_get_json)
    wunderground.hourly()
    assert "forecast/hourly" in seen["url"]
    assert seen["params"]["geocode"] == wunderground.KDFW_GEOCODE
    assert seen["params"]["units"] == "e"
    assert seen["params"]["apiKey"] == wunderground.WEB_API_KEY


def test_hourly_empty_feed_returns_empty_list(monkeypatch):
    monkeypatch.setattr(wunderground, "get_json",
                        lambda url, params, **kw: {"validTimeUtc": []})
    assert wunderground.hourly() == []


def test_pws_current_returns_temp_and_obs_time(monkeypatch):
    payload = {"observations": [{
        "stationID": "KTXEULES41",
        "obsTimeUtc": "2026-07-18T04:16:40Z",
        "imperial": {"temp": 85},
    }]}
    monkeypatch.setattr(wunderground, "get_json", lambda url, params, **kw: payload)
    out = wunderground.pws_current()
    assert out["temp"] == 85
    assert isinstance(out["obs_time"], datetime)
    assert out["obs_time"].tzinfo is not None


def test_pws_current_empty_returns_none(monkeypatch):
    monkeypatch.setattr(wunderground, "get_json",
                        lambda url, params, **kw: {"observations": []})
    assert wunderground.pws_current() is None


def test_pws_current_requests_the_euless_station(monkeypatch):
    seen = {}

    def fake_get_json(url, params, **kw):
        seen["params"] = params
        return {"observations": [{"obsTimeUtc": "2026-07-18T04:16:40Z",
                                  "imperial": {"temp": 85}}]}

    monkeypatch.setattr(wunderground, "get_json", fake_get_json)
    wunderground.pws_current()
    assert seen["params"]["stationId"] == wunderground.PWS_STATION_ID
