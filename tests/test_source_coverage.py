"""Same-day extreme coverage: a now-forward source (NWS / LAMP / NBM) must not
define a daily extreme whose defining window it never saw.

A forecast that only spans now->forward has no early-morning hours, so its
'low' for the current day is really the afternoon/evening minimum (e.g. 86°F
when the true overnight low was 77°F). Such a source must abstain from that
extreme rather than contaminate the per-source panel and the spread reference.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE
from settlement import covers_extreme

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
