"""Same-day extreme coverage: a now-forward source (NWS / LAMP / NBM) must not
define a daily extreme whose defining window it never saw.

A forecast that only spans now->forward has no early-morning hours, so its
'low' for the current day is really the afternoon/evening minimum (e.g. 86°F
when the true overnight low was 77°F). Such a source must abstain from that
extreme rather than contaminate the per-source panel and the spread reference.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE
from settlement import covers_extreme
from sources import station_history

_TZ = ZoneInfo(TIMEZONE)


def _series(day, hours, peak_hour=15, base=70.0, amp=16.0):
    """A diurnal temp curve sampled only at the given local `hours` of `day`."""
    start = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [start + timedelta(hours=h) for h in hours]
    temps = [base + amp - abs(h - peak_hour) for h in hours]
    return times, temps


DAY = datetime(2026, 6, 23, tzinfo=_TZ).date()


def test_full_day_series_covers_both_extremes():
    times, temps = _series(DAY, range(24))
    assert covers_extreme(times, temps, DAY, "low") is True
    assert covers_extreme(times, temps, DAY, "high") is True


def test_afternoon_only_series_misses_the_low():
    # noon -> midnight: never saw the morning, so the low is uncovered.
    times, temps = _series(DAY, range(12, 24))
    assert covers_extreme(times, temps, DAY, "low") is False
    assert covers_extreme(times, temps, DAY, "high") is True


def test_evening_only_series_misses_both():
    # 8pm onward: missed both the morning low and the mid-afternoon peak.
    times, temps = _series(DAY, range(20, 24))
    assert covers_extreme(times, temps, DAY, "low") is False
    assert covers_extreme(times, temps, DAY, "high") is False


def test_morning_start_series_misses_the_low():
    # A same-day now-forward fetch from 7am on (LAMP/NWS refreshed mid-morning):
    # the dawn low already passed, so its forward hours at clock 7-9 must not
    # count as low coverage — otherwise it reports the evening tail as the "low"
    # (live 2026-07-18: mos_lav logged 83-84 against a settled-77 night).
    times, temps = _series(DAY, range(7, 24))
    assert covers_extreme(times, temps, DAY, "low") is False
    assert covers_extreme(times, temps, DAY, "high") is True


def test_pre_dawn_start_series_covers_the_low():
    # A fetch from 5am still has the dawn minimum ahead of it.
    times, temps = _series(DAY, range(5, 24))
    assert covers_extreme(times, temps, DAY, "low") is True


def test_summer_tail_alone_is_not_low_coverage():
    # Under the LST window the day's final hour is clock 00:xx of the next
    # day. A series holding only the evening plus that tail (hour 24 below)
    # never saw dawn, so it cannot claim the day's low.
    times, temps = _series(DAY, range(20, 25))
    assert covers_extreme(times, temps, DAY, "low") is False


def test_per_source_extremes_nulls_uncovered_low():
    series = {"nws_ndfd": _series(DAY, range(12, 24))}
    out = model.per_source_extremes(series, DAY)
    # the source still reports its (valid) high, but abstains on the low
    entry = out["nws"]["nws_ndfd"]
    hi, lo = entry
    assert hi is not None
    assert lo is None


def test_member_extreme_abstains_from_uncovered_low_in_pure_path():
    times, temps = _series(DAY, range(12, 24))
    # now=None is the pure / full-day reference path used for the spread anchor.
    assert model._member_extreme(times, temps, DAY, "low", None, None) is None
    # the high is still covered, so it is returned normally.
    assert model._member_extreme(times, temps, DAY, "high", None, None) is not None


def test_snapshot_requests_enough_forecast_days_for_settlement_tail(monkeypatch):
    """Tomorrow's settlement day (LST climate day) ends at 01:00 CDT the day
    after tomorrow, past Open-Meteo's clock-day hourly cutoff at
    forecast_days=2. snapshot() must request at least 3 forecast days so its
    members aren't blind to that final settlement hour."""
    captured = {}

    def _fake_gather_series(forecast_days=2, continuous_obs=False, now=None):
        captured["forecast_days"] = forecast_days
        times, temps = _series(DAY, range(24))
        series = {"nws_ndfd": (times, temps)}
        obs = {"obs": (times, temps)}
        return series, obs, []

    monkeypatch.setattr(model, "gather_series", _fake_gather_series)
    model.snapshot()
    assert captured["forecast_days"] >= 3


def test_fetch_actual_widens_raw_fetch_past_end_for_lst_tail(monkeypatch):
    """IEM asos.py's day2 param is exclusive, so a raw fetch bounded at `end`
    misses `end`'s LST settlement tail (00:00-00:59 the next clock day).
    fetch_actual must widen the underlying _fetch_series call by one day while
    still emitting only start..end inclusive."""
    start = date(2026, 7, 10)
    end = date(2026, 7, 11)
    seen = {}

    def _fake_fetch_series(s, e):
        seen["start"], seen["end"] = s, e
        # A boundary night: end's true low (68) only shows up in the LST tail,
        # i.e. clock hour 0 of the day AFTER end -- only present if the raw
        # fetch was widened past `end`.
        base = datetime(end.year, end.month, end.day, tzinfo=_TZ)
        times, temps = [], []
        for h in range(24):
            times.append(base - timedelta(hours=24 - h))
            temps.append(80.0)  # start: flat warm day
        for h in range(24):
            times.append(base + timedelta(hours=h))
            temps.append(80.0)  # end: flat warm day ...
        # ... except its LST tail (hour 0 of end+1), which is the real low.
        times.append(base + timedelta(days=1))
        temps.append(68.0)
        return times, temps

    monkeypatch.setattr(station_history, "_fetch_series", _fake_fetch_series)
    monkeypatch.setattr(station_history, "to_hourly", lambda t, te: (t, te))

    out = station_history.fetch_actual(start, end)

    # Raw fetch widened one day past `end` ...
    assert seen["end"] == end + timedelta(days=1)
    # ... but only start..end are emitted, and end's low reflects its LST tail.
    assert set(out.keys()) == {start, end}
    assert out[end][1] == 68.0
