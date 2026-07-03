"""Time-gated early high lock + the CLI offset gate at a realized-but-plateaued
peak (the "reads 95.9 at 6pm when it's already 95" double-count bug)."""
from datetime import date, datetime, timedelta

import model
from config import TIMEZONE
from zoneinfo import ZoneInfo

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 2)


def _hours(*hhtemp):
    """[(hour, temp), ...] -> (times, temps) on _DAY, local tz."""
    times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, tzinfo=_TZ) for h, _ in hhtemp]
    temps = [t for _, t in hhtemp]
    return times, temps


def _at(hour):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, tzinfo=_TZ)


# --- Fix #2: time-gated early high lock (mirror of the sunrise-gated low) ---

def test_high_early_locks_after_hour_on_small_drop():
    # Peak 95 @15:00, eased to 94.0 @17:00 (drop 1.0 < 2.0). Past the lock hour.
    times, temps = _hours((6, 72), (9, 82), (13, 92), (15, 95), (17, 94.0))
    assert model._extreme_locked(times, temps, _DAY, "high", _at(17)) is True


def test_high_stays_unlocked_before_hour_under_small_drop():
    # Same small drop but at 14:00 (before the lock hour) -> the 2°F rule governs,
    # so a 1°F easing must NOT lock.
    times, temps = _hours((6, 72), (9, 82), (13, 95), (14, 94.0))
    assert model._extreme_locked(times, temps, _DAY, "high", _at(14)) is False


def test_high_no_false_lock_while_still_at_peak():
    # Past the hour but the current reading IS the running max (still climbing /
    # sitting exactly on the peak, drop 0) -> not off the peak yet, no lock.
    times, temps = _hours((6, 72), (9, 82), (15, 94), (17, 95))
    assert model._extreme_locked(times, temps, _DAY, "high", _at(17)) is False


def test_high_2f_fallback_still_fires_before_hour():
    # A full 2°F drop locks regardless of time of day (fallback unchanged).
    times, temps = _hours((6, 72), (9, 82), (13, 95), (14, 92.5))  # drop 2.5
    assert model._extreme_locked(times, temps, _DAY, "high", _at(14)) is True


def test_high_early_lock_requires_peak_postdates_trough():
    # Warm-overnight artifact: running max (90 @00:00) precedes the morning min,
    # so a retreat from it late in the day must NOT lock even past the hour.
    times, temps = _hours((0, 90), (3, 85), (6, 72), (17, 89))  # drop 1.0, not peaked
    assert model._extreme_locked(times, temps, _DAY, "high", _at(17)) is False


# --- Fix #1: CLI offset gate at a realized (past-hour) peak, lock or no lock ---

def _obs(day, temps, continuous):
    """Hourly obs from `temps` (one per hour from midnight), plus a finer
    continuous feed mirroring them so observed_cont is set (gap 0)."""
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    ot = [base + timedelta(hours=h) for h in range(len(temps))]
    obs = {"obs": (ot, temps)}
    if continuous:
        ct = [base + timedelta(minutes=15 * k) for k in range(len(temps) * 4)]
        cv = [temps[k // 4] for k in range(len(temps) * 4)]
        obs["obs_continuous"] = (ct, cv)
    return obs


def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]
    return times, temps


def _series(day):
    return {"det_a": _member(day, 90.0), "det_b": _member(day, 92.0)}


# A realistic day that has peaked (95 @15:00) and eased only 0.5°F by 17:00 — not
# enough to trip either lock, exactly the plateau where the model was reading hot.
_PLATEAU = [80, 79, 78, 77, 76, 75, 76, 78, 81, 84, 87, 89, 91, 93, 94, 95, 95, 94.5]
_OFF = {"high": 0.89, "high_std": 0.77, "low": 0.0, "low_std": 0.0}


def test_high_offset_gate_no_double_count_at_plateau():
    day = date(2026, 7, 2)
    series = _series(day)
    now = _at(17)
    with_cont = model.predict_variable(series, _obs(day, _PLATEAU, True),
                                       day, "high", now, None, _OFF)
    no_cont = model.predict_variable(series, _obs(day, _PLATEAU, False),
                                     day, "high", now, None, _OFF)
    no_offset = model.predict_variable(series, _obs(day, _PLATEAU, True),
                                       day, "high", now, None, None)
    # Still not "locked" (only eased 0.5°F), but the peak is realized past the hour.
    assert with_cont["peak_locked"] is False
    # Measured gap is 0, so the center matches the no-offset (hourly) center — the
    # phantom +0.89 average offset is NOT layered on top of the observed peak.
    assert with_cont["consensus"] == no_offset["consensus"]
    # Without the continuous feed the average offset still applies -> higher.
    assert with_cont["consensus"] < no_cont["consensus"]


def test_predict_variable_high_early_locks_and_anchors():
    # Same shape but eased a full 1°F by 17:00 -> early lock fires (2°F rule alone
    # would leave it unlocked), collapsing to the realized peak with gap 0.
    day = date(2026, 7, 2)
    dropped = _PLATEAU[:-1] + [94.0]   # 17:00 reads 94.0, drop 1.0
    out = model.predict_variable(_series(day), _obs(day, dropped, True),
                                 day, "high", _at(17), None, _OFF)
    assert out["peak_locked"] is True
    assert out["consensus"] == 95.0     # realized peak, no phantom offset
