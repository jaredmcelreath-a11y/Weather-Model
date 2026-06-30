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
