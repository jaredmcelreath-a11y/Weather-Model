"""Front-aware locked low: a locked member reports its anchored post-noon
forecast minimum instead of the observed min when that projection undercuts it
by FRONT_UNDERCUT_MARGIN — so a dry evening cold front (which the POP-gated
convective floor can't see) reopens the low instead of being discarded."""
from datetime import date, datetime

import model
from config import TIMEZONE
from zoneinfo import ZoneInfo

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 2)


def _at(hour, minute=0):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, minute, tzinfo=_TZ)


def _fc(curve):
    """{hour: temp} -> full-day hourly (times, temps) forecast series.
    Hours not listed interpolate nothing — list every hour you need."""
    hours = sorted(curve)
    return ([_at(h) for h in hours], [curve[h] for h in hours])


def _curve(evening):
    """A standard day shape: cool dawn, warm afternoon, then `evening` values
    for hours 18/21/23. Morning min ~78, peak 95 at 15:00."""
    base = {0: 84, 2: 82, 4: 80, 6: 78, 8: 82, 10: 86, 12: 90, 13: 92,
            14: 93, 15: 95, 16: 94, 17: 92}
    base.update(evening)
    return base


# ---- the locked-low undercut path (unit: _member_extreme directly) ----

def test_calm_locked_low_returns_observed():
    # Evening stays well above the 78.0 morning min -> locked exactly as today.
    times, temps = _fc(_curve({18: 88, 21: 84, 23: 81}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 78.0


def test_front_undercut_reports_forecast_min():
    # Front: evening drops to 74.5 (3.5 under the observed min) -> the member
    # reports its projected new low, not the stale morning min.
    times, temps = _fc(_curve({18: 80, 21: 76, 23: 74.5}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 74.5


def test_pre_noon_dip_cannot_trigger():
    # A 9am forecast dip 2 under the min (dawn-adjacent jitter, the reason the
    # early sunrise lock exists) must NOT reopen the lock: scan starts at 12:00.
    curve = _curve({18: 88, 21: 84, 23: 81})
    curve[9] = 76.0                      # pre-noon dip, still in `remaining` at 08:00
    times, temps = _fc(curve)
    got = model._member_extreme(times, temps, _DAY, "low", _at(8),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 78.0


def test_margin_graze_ignored():
    # Post-noon min 77.7 vs observed 78.0: undercut 0.3 < 0.5 margin -> locked.
    times, temps = _fc(_curve({18: 82, 21: 79, 23: 77.7}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                observed=78.0, obs_now=None, locked=True)
    assert got == 78.0


def test_no_remaining_postnoon_hours_falls_back():
    # 23:30 with the last forecast point at 23:00 (already past): nothing left
    # to scan -> observed, no crash.
    times, temps = _fc(_curve({18: 80, 21: 76, 23: 74.5}))
    got = model._member_extreme(times, temps, _DAY, "low", _at(23, 30),
                                observed=74.5, obs_now=None, locked=True)
    assert got == 74.5


def test_anchoring_offset_applies_to_scan():
    # Raw post-noon min is 78.2 (no trigger vs 78.0), but the member currently
    # reads 1°F warm (obs_now 89 vs fc_now 90 at 13:00 — the curve's 13:00 value
    # is pinned to 90 so the interpolated fc_now is exact), so its anchored
    # evening projection is 77.2 -> undercut fires at the ANCHORED value.
    curve = _curve({18: 82, 21: 79.5, 23: 78.2})
    curve[13] = 90.0                     # fc_now at 13:00 -> offset = 89 - 90 = -1
    times, temps = _fc(curve)
    unanchored = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                       observed=78.0, obs_now=None, locked=True)
    anchored = model._member_extreme(times, temps, _DAY, "low", _at(13),
                                     observed=78.0, obs_now=89.0, locked=True)
    assert unanchored == 78.0
    assert anchored == 77.2


def test_locked_high_still_pins_to_observed():
    # The high's locked branch is untouched: a forecast projecting hotter later
    # is still ignored once locked (peak-postdates-trough guard owns the high).
    times, temps = _fc(_curve({18: 96, 21: 90, 23: 86}))
    got = model._member_extreme(times, temps, _DAY, "high", _at(16),
                                observed=95.0, obs_now=None, locked=True)
    assert got == 95.0
