"""Sunrise-gated early low lock."""
from datetime import date, datetime

import model
from config import TIMEZONE
from zoneinfo import ZoneInfo

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 2)          # KDFW sunrise ~06:23 CDT


def _hours(*hhtemp):
    """[(hour, temp), ...] -> (times, temps) on _DAY, local tz."""
    times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, tzinfo=_TZ) for h, _ in hhtemp]
    temps = [t for _, t in hhtemp]
    return times, temps


def _at(hour):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, tzinfo=_TZ)


def test_low_early_locks_after_sunrise_on_small_rise():
    # Min 78.8 at 06:00, risen to 80.0 by 07:00 (risen 1.2 < 2.0). Past sunrise.
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8), (7, 80.0))
    assert model._extreme_locked(times, temps, _DAY, "low", _at(7)) is True


def test_low_stays_unlocked_before_sunrise_under_small_rise():
    # Same shape but at 05:00 (before sunrise) with <2°F rise -> not locked.
    times, temps = _hours((0, 84), (2, 82), (4, 80), (5, 78.8))
    assert model._extreme_locked(times, temps, _DAY, "low", _at(5)) is False


def test_low_no_predawn_false_lock():
    # A pre-dawn wiggle: min 79 at 03:00, up to 80 at 04:00 (risen 1.0 >= 0.8)
    # but 04:00 is before sunrise -> must NOT lock.
    times, temps = _hours((0, 82), (1, 81), (2, 80), (3, 79.0), (4, 80.0))
    assert model._extreme_locked(times, temps, _DAY, "low", _at(4)) is False


def test_low_2f_fallback_still_fires_before_sunrise():
    # A full 2°F rise locks regardless of time of day (fallback unchanged).
    times, temps = _hours((0, 82), (2, 80), (3, 79.0), (4, 81.5))   # risen 2.5
    assert model._extreme_locked(times, temps, _DAY, "low", _at(4)) is True


def test_high_branch_unaffected_by_morning_rise():
    # The same rising-morning series, asked for the HIGH, must not lock: the
    # running max (midnight) precedes the running min, so the high guard holds.
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8), (7, 80.0))
    assert model._extreme_locked(times, temps, _DAY, "high", _at(7)) is False


def test_predict_variable_locks_low_earlier():
    # Integration: rising morning obs, now 07:00 -> low locks to the observed
    # minimum, where the 2°F rule (risen 1.2) would still leave it unlocked.
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8), (7, 80.0))
    fc_times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, tzinfo=_TZ) for h in range(24)]
    series = {"det_a": (fc_times, [90 - abs(h - 15) for h in range(24)])}
    out = model.predict_variable(series, {"obs": (times, temps)}, _DAY, "low",
                                 _at(7), None)
    assert out["peak_locked"] is True
    assert out["consensus"] == 78.8            # locked to the realized min
