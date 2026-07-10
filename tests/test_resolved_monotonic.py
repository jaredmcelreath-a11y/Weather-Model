"""The 'Resolved' metric must not go backwards during the day (2026-07-09).

Resolved was derived from `locked_ratio = std(samples)/fullday_sd` — the momentary
agreement among the ensemble's projected peaks — which swings up and down as the
temperature plateaus and resumes climbing (observed on 2026-07-06: 53->97->42->0->100).
It now derives from the hard bound (`observed_so_far`, which only ratchets), so it is
monotonic non-decreasing through the day and hits 100% at lock.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]   # peaks at 15:00
    return times, temps


# A day that climbs to 98 by ~14:00, holds, then eases (so the peak locks by evening).
_HILL = [78, 79, 80, 82, 84, 86, 88, 90, 92, 93, 94, 95, 96, 97, 98, 98, 96, 95]


def _obs_hourly(day, temps):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    return {"obs": ([base + timedelta(hours=h) for h in range(len(temps))], list(temps))}


def test_resolved_non_decreasing_through_the_day():
    day = date(2030, 7, 1)
    series = {"det_a": _member(day, 97.0), "det_b": _member(day, 99.0)}
    obs = _obs_hourly(day, _HILL)
    resolved = []
    for h in range(10, 20):
        now = datetime(day.year, day.month, day.day, h, tzinfo=_TZ)
        r = model.predict_variable(series, obs, day, "high", now, None)
        resolved.append(r["resolved"])
    assert all(b >= a - 1e-9 for a, b in zip(resolved, resolved[1:])), resolved
    assert resolved[-1] == 1.0          # window closed (past 18:00) -> fully resolved
    assert resolved[0] < 0.5            # nothing resolved mid-morning


def test_low_resolved_non_decreasing():
    # Symmetric for the low: obs_min ratchets down, resolution only rises.
    day = date(2030, 7, 1)
    series = {"det_a": _member(day, 90.0), "det_b": _member(day, 92.0)}
    # Descending overnight to a 74 low by 06:00, then rising (locks the low).
    temps = [82, 81, 80, 79, 78, 76, 74, 74, 76, 80, 84]
    obs = _obs_hourly(day, temps)
    resolved = []
    for h in range(3, 11):
        now = datetime(day.year, day.month, day.day, h, tzinfo=_TZ)
        r = model.predict_variable(series, obs, day, "low", now, None)
        resolved.append(r["resolved"])
    assert all(b >= a - 1e-9 for a, b in zip(resolved, resolved[1:])), resolved
    assert resolved[-1] == 1.0          # past 09:00 low window -> fully resolved
