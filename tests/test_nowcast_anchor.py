"""The forecast anchor must track `now` continuously, not step at the whole hour.

Regression for the sawtooth dip in the consensus: `_member_extreme` anchored the
remaining forecast to the forecast value at the *integer* current hour (a step
function), while the observation anchor updates only at the routine :53 reading.
So right after the clock ticked past the hour, the forecast anchor jumped up but
the observation hadn't — collapsing the offset and dropping the projected high a
few degrees, then recovering. Interpolating the anchor to the exact `now`
removes the discontinuity.
"""
from datetime import date, datetime

import model
from config import TIMEZONE
from zoneinfo import ZoneInfo

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 2)


def _hours(*hhtemp):
    times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, tzinfo=_TZ) for h, _ in hhtemp]
    temps = [t for _, t in hhtemp]
    return times, temps


def _at(hour, minute=0):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, minute, tzinfo=_TZ)


# A rising morning forecast that peaks at 99 in mid-afternoon.
_FCST = _hours((6, 72), (7, 76), (8, 80), (9, 84), (10, 88), (11, 91),
               (12, 94), (13, 96), (14, 98), (15, 99), (16, 98), (17, 95))


def test_no_hour_boundary_dip():
    """With the observation anchor held constant across the hour flip (as the
    :53 hourly reading is), the projected high must not jump when the clock ticks
    from 9:59 to 10:01."""
    times, temps = _FCST
    obs_now = observed = 86.0  # last routine reading, unchanged across the flip
    before = model._member_extreme(times, temps, _DAY, "high",
                                   _at(9, 59), observed, obs_now=obs_now)
    after = model._member_extreme(times, temps, _DAY, "high",
                                  _at(10, 1), observed, obs_now=obs_now)
    assert abs(before - after) < 0.5, (before, after)


def test_anchor_interpolates_between_hours():
    """Mid-hour, the anchor offset should reflect the interpolated forecast, so a
    perfectly-tracking observation yields ~zero offset (projected high == peak)."""
    times, temps = _FCST
    # Real temp exactly matches the forecast interpolated to 10:30 (86+ (88->91)/2).
    interp_1030 = 88 + (91 - 88) * 0.5
    got = model._member_extreme(times, temps, _DAY, "high",
                                _at(10, 30), observed=interp_1030, obs_now=interp_1030)
    assert abs(got - 99.0) < 0.3, got


def test_anchor_obs_now_is_a_20min_mean_that_dampens_a_spike():
    """The offset anchor is the mean of the last 4 (~20 min), so a lone whole-degC
    spike is diluted rather than taken at face value (which swung the projected peak)."""
    assert model._anchor_obs_now([95, 95, 95, 95]) == 95
    assert model._anchor_obs_now([95, 95, 95, 99]) == 96.0     # a +4 spike -> +1 anchor
    assert model._anchor_obs_now([80, 80, 95, 95, 95, 95]) == 95   # only the last 4
    assert model._anchor_obs_now([90, 92]) == 91.0                 # fewer than 4 -> mean of all
