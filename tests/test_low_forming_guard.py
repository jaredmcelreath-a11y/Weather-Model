"""Dawn-low "still forming" guard.

Root cause (2026-07-24): the low's `resolved` metric is inflated by a clock term
(tprog runs midnight->9am, so ~75% by 6:30) while the true summer minimum lands
at/after sunrise (~6:35) and often keeps dipping to 7:00-7:30. That made the
Resolved card read ~90% and `lock_status` flash a green "prime buy window" 40
minutes BEFORE the trough physically locked — then temps dropped 2-3°F into the
next bracket. Real example: 2026-07-24 held ~82°F through 6:50, settled ~79.3.

The guard: while today's low is not yet physically locked (`peak_locked` False),
mark it `low_forming` so (a) the Resolved card shows only the physically ruled-out
mass (strip the clock inflation), (b) lock_status refuses the green buy badge, and
(c) sigma is floored so the bins hedge toward the still-possible colder readings.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE, LOW_FORMING_SIGMA_MIN, LOW_FORMING_RESOLVED_CAP
from market_view import lock_status, displayed_resolved

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 7, 2)          # KDFW sunrise ~06:23 CDT


def _hours(*hhtemp):
    times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, tzinfo=_TZ) for h, _ in hhtemp]
    temps = [t for _, t in hhtemp]
    return times, temps


def _at(hour):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, tzinfo=_TZ)


# ---- displayed_resolved: strip clock inflation while forming --------------

def test_displayed_resolved_strips_clock_inflation_while_forming():
    # Both the clock term (0.90) and a high one-sided collapse must be capped:
    # forming = definitionally unsettled, so the card reads "half-open".
    d = {"resolved": 0.90, "resolved_collapse": 0.95, "low_forming": True}
    assert displayed_resolved(d) == LOW_FORMING_RESOLVED_CAP


def test_displayed_resolved_full_once_locked():
    d = {"resolved": 0.90, "resolved_collapse": 0.50, "low_forming": False}
    assert displayed_resolved(d) == 90


def test_displayed_resolved_old_snapshot_unaffected():
    # No low_forming / resolved_collapse keys (older logs) -> unchanged.
    assert displayed_resolved({"resolved": 0.90}) == 90


# ---- lock_status: no green buy badge while forming ------------------------

def _low(**over):
    d = {
        "locked_ratio": 0.2,
        "resolved": 0.90,          # clock-inflated
        "resolved_collapse": 0.50,
        "observed_so_far": 81.0,
        "consensus": 82.0,
        "sigma_used": 1.5,
        "peak_locked": False,
        "low_forming": True,
        "convective_widened": False,
        "front_widened": False,
    }
    d.update(over)
    return d


def test_lock_status_forming_low_not_prime_buy_window():
    level, headline, detail = lock_status(_low(), "low")
    assert level != "success"
    assert "prime buy window" not in detail.lower()
    assert "form" in (headline + detail).lower() or "wait" in (headline + detail).lower()


def test_lock_status_locked_low_still_green():
    level, headline, _ = lock_status(
        _low(peak_locked=True, low_forming=False), "low")
    assert level == "success"
    assert headline == "Locked — Dawn Trough Is In"


# ---- predict_variable integration ----------------------------------------

def _series():
    fc_times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, tzinfo=_TZ) for h in range(24)]
    return {"det_a": (fc_times, [90 - abs(h - 15) for h in range(24)])}


def test_predict_variable_flags_forming_and_floors_sigma():
    # 06:00, before sunrise, temps still falling -> low not locked yet.
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8))
    out = model.predict_variable(_series(), {"obs": (times, temps)}, _DAY, "low",
                                 _at(6), None)
    assert out["peak_locked"] is False
    assert out["low_forming"] is True
    assert out["sigma_used"] >= LOW_FORMING_SIGMA_MIN
    # Card no longer over-reports: capped to the forming ceiling, not the clock.
    assert displayed_resolved(out) <= LOW_FORMING_RESOLVED_CAP


def test_predict_variable_locked_low_not_forming():
    # 07:00, risen 1.2°F past sunrise -> early-lock fires, guard releases.
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8), (7, 80.0))
    out = model.predict_variable(_series(), {"obs": (times, temps)}, _DAY, "low",
                                 _at(7), None)
    assert out["peak_locked"] is True
    assert out["low_forming"] is False


def test_high_never_forming():
    times, temps = _hours((0, 84), (2, 82), (4, 80), (6, 78.8))
    out = model.predict_variable(_series(), {"obs": (times, temps)}, _DAY, "high",
                                 _at(6), None)
    assert out["low_forming"] is False
