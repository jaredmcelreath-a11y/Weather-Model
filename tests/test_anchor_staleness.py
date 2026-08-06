"""The nowcast anchor is differenced against the forecast at the anchor's OWN
instant, not at `now`.

`obs_now` is a mean of the last ~4 sub-hourly readings, so the temperature it
represents is from the middle of that window — plus NWS's publication lag. Measured
live 2026-08-06 at KDFW: 14.4 min publish lag + ~5 min of averaging = ~20 min behind
the clock. Comparing that against the forecast interpolated to `now` compares two
different instants, so on a warming morning (+3°F/hr) the member looks ~1°F colder
than it is — every day, in the same direction. Interpolating the forecast to the
anchor's real timestamp removes it.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 8, 4)


def _at(hour, minute=0):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, minute, tzinfo=_TZ)


def _hours(*hhtemp):
    return ([_at(h) for h, _ in hhtemp], [t for _, t in hhtemp])


# Steady +3°F/hr morning ramp to a 101 peak — the regime where the lag bites.
_RAMP = ((6, 74.0), (7, 77.0), (8, 80.0), (9, 83.0), (10, 86.0),
         (12, 95.0), (14, 99.0), (16, 101.0), (18, 98.0))


def test_perfectly_tracking_member_gets_no_offset():
    """The member's forecast for 08:40 is exactly what the anchor reports for
    08:40. Its error is zero, so its peak must come through unshifted — even
    though the pass runs at 09:00."""
    times, temps = _hours(*_RAMP)
    got = model._member_extreme(times, temps, _DAY, "high", _at(9),
                                observed=83.0, obs_now=82.0,
                                obs_now_at=_at(8, 40))
    assert abs(got - 101.0) < 1e-6, f"stale anchor manufactured an offset: {got}"


def test_lag_manufactured_a_cold_offset_without_the_timestamp():
    """Same anchor, no timestamp -> compared against the 09:00 forecast (83.0)
    and so read as a -1.0°F error. This is the old behaviour, kept as the
    contrast case."""
    times, temps = _hours(*_RAMP)
    got = model._member_extreme(times, temps, _DAY, "high", _at(9),
                                observed=83.0, obs_now=82.0)
    assert got < 101.0


def test_real_error_survives_the_correction():
    """A member genuinely running 2°F warm at 08:40 (anchor 81.0 vs its 82.0
    forecast) still gets shifted — the fix removes the artefact, not the signal."""
    times, temps = _hours(*_RAMP)
    tracking = model._member_extreme(times, temps, _DAY, "high", _at(9),
                                     observed=83.0, obs_now=82.0,
                                     obs_now_at=_at(8, 40))
    warm = model._member_extreme(times, temps, _DAY, "high", _at(9),
                                 observed=83.0, obs_now=81.0,
                                 obs_now_at=_at(8, 40))
    assert warm < tracking


def test_evening_cooling_lag_corrects_the_other_way():
    """The artefact is signed by the ramp, so on the evening cooldown the stale
    anchor read too WARM; correcting it must move the projection down, not up."""
    times, temps = _hours(*_RAMP)
    stale = model._member_extreme(times, temps, _DAY, "low", _at(17),
                                  observed=None, obs_now=99.0)
    fixed = model._member_extreme(times, temps, _DAY, "low", _at(17),
                                  observed=None, obs_now=99.0,
                                  obs_now_at=_at(16, 40))
    assert fixed < stale


def test_anchor_at_is_clamped_to_now():
    """A timestamp at/after `now` (clock skew) must not extrapolate the forecast
    forward — it degrades to the old `now` behaviour."""
    times, temps = _hours(*_RAMP)
    ahead = model._member_extreme(times, temps, _DAY, "high", _at(9),
                                  observed=83.0, obs_now=82.0,
                                  obs_now_at=_at(9, 30))
    at_now = model._member_extreme(times, temps, _DAY, "high", _at(9),
                                   observed=83.0, obs_now=82.0)
    assert abs(ahead - at_now) < 1e-9


def test_anchor_window_reports_value_and_middle_timestamp():
    """The effective instant of a 4-reading mean is the mean of their times."""
    pairs = [(_at(8, 40), 80.0), (_at(8, 45), 81.0),
             (_at(8, 50), 82.0), (_at(8, 55), 83.0)]
    val, when = model._anchor_window(pairs)
    assert val == 81.5
    assert when == _at(8, 47, ) + timedelta(seconds=30)


def test_anchor_window_matches_the_value_only_helper():
    pairs = [(_at(8, h), v) for h, v in ((40, 95.0), (45, 95.0), (50, 95.0), (55, 99.0))]
    val, _ = model._anchor_window(pairs)
    assert val == model._anchor_obs_now([95, 95, 95, 99])


def test_latest_obs_returns_its_timestamp():
    """The hourly path anchors on the routine :53 METAR, which can be nearly an
    hour behind `now` — it needs its timestamp even more than the 5-min feed."""
    times = [_at(7, 53), _at(8, 53)]
    temps = [77.0, 80.5]
    val, when = model._latest_obs(times, temps, _DAY, _at(9, 40))
    assert val == 80.5
    assert when == _at(8, 53)
