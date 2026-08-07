"""Tests for the live observation fetch window.

The daily low occurs in the early morning. A snapshot taken late in the evening
must still see that morning minimum, or the same-day low anchors to the evening
cooldown and prints several degrees warm. The fetch therefore has to request the
*whole* local day, not a fixed count of sub-hourly readings (~13/hr => a 200-cap
spans only ~15h, which from a 23:45 capture starts after the 6am low).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
from sources import nws_observations

TZ = ZoneInfo(TIMEZONE)


def _one_feature(iso: str, temp_c: float) -> dict:
    return {"features": [{"properties": {"timestamp": iso,
                                         "temperature": {"value": temp_c}}}]}


def test_fetch_requests_full_local_day(monkeypatch):
    seen = {}

    def fake_get_json(url, params=None, **kw):
        seen["params"] = params or {}
        return _one_feature("2026-06-30T06:00:00-05:00", 26.0)

    monkeypatch.setattr(nws_observations, "get_json", fake_get_json)

    # A late-evening capture: the morning low is ~18 hours behind us.
    now = datetime(2026, 6, 30, 23, 45, tzinfo=TZ)
    nws_observations.fetch(now=now)

    assert "start" in seen["params"], "fetch must bound the window by start time"
    start = datetime.fromisoformat(seen["params"]["start"]).astimezone(TZ)
    midnight = datetime(2026, 6, 30, 0, 0, tzinfo=TZ)
    # The window must reach back to (at least) local midnight so the morning low
    # of the current settlement day is always covered, regardless of capture time.
    assert start <= midnight


def test_latest_takes_a_raw_station_id(monkeypatch):
    # Reference cities on the Hourly page are not in config.STATIONS, so this
    # must not route through config.station().
    seen = {}

    def fake_get_json(url, params=None, **kw):
        seen["url"] = url
        seen["params"] = params or {}
        return _one_feature("2026-08-07T18:53:00+00:00", 30.0)

    monkeypatch.setattr(nws_observations, "get_json", fake_get_json)
    got = nws_observations.latest("KATL")
    assert seen["url"] == "https://api.weather.gov/stations/KATL/observations"
    assert got["temp"] == 86.0
    assert got["time"].utcoffset().total_seconds() == 0


def test_latest_skips_readings_with_no_temperature(monkeypatch):
    payload = {"features": [
        {"properties": {"timestamp": "2026-08-07T18:53:00+00:00",
                        "temperature": {"value": None}}},
        {"properties": {"timestamp": "2026-08-07T18:48:00+00:00",
                        "temperature": {"value": 30.0}}},
    ]}
    monkeypatch.setattr(nws_observations, "get_json", lambda *a, **k: payload)
    got = nws_observations.latest("KATL")
    # The feed is newest-first, so the first usable reading is the answer.
    assert got["temp"] == 86.0
    assert got["time"].minute == 48


def test_latest_returns_none_on_an_empty_feed(monkeypatch):
    monkeypatch.setattr(nws_observations, "get_json", lambda *a, **k: {"features": []})
    assert nws_observations.latest("KATL") is None


def test_latest_returns_none_when_the_feed_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nws down")

    monkeypatch.setattr(nws_observations, "get_json", boom)
    assert nws_observations.latest("KATL") is None
