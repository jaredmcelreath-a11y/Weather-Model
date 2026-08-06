"""Lead-decayed nowcast anchor.

The obs-vs-forecast offset `(obs_now - fc_now)` used to shift every remaining
forecast hour 1:1, so a morning gap was stamped onto the afternoon peak seven
hours away. Measured on the logged days (2026-08-06), that made the 9am same-day
high undershoot by 1.6°F at KDFW and 3.1°F at KAUS while the *raw* model peaks
were near-unbiased. The offset now fades with distance from `now`
(ANCHOR_DECAY_HOURS), so it still tracks the near term — which is what makes the
afternoon follow reality down — without extrapolating a transient morning
timing error onto the peak.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import model
from config import ANCHOR_DECAY_HOURS, TIMEZONE

_TZ = ZoneInfo(TIMEZONE)
_DAY = date(2026, 8, 4)


def _hours(*hhtemp):
    times = [datetime(_DAY.year, _DAY.month, _DAY.day, h, tzinfo=_TZ) for h, _ in hhtemp]
    return times, [t for _, t in hhtemp]


def _at(hour):
    return datetime(_DAY.year, _DAY.month, _DAY.day, hour, tzinfo=_TZ)


# A plain warming day: forecast peaks 101 at 16:00.
_CURVE = ((6, 80.0), (9, 86.0), (12, 95.0), (14, 99.0), (16, 101.0), (18, 98.0))


def test_morning_gap_barely_moves_the_afternoon_peak():
    """9am pass, member reading 2.4°F cool vs its own 9am forecast. The peak is
    7h out, so almost none of that gap should reach it (the old code returned
    the full 101.0 - 2.4 = 98.6)."""
    times, temps = _hours(*_CURVE)
    got = model._member_extreme(times, temps, _DAY, "high", _at(9),
                                observed=86.0, obs_now=83.6)
    assert got > 100.0, f"morning gap over-applied to the peak: {got}"
    assert got < 101.0, "some near-term anchoring should still apply"


def test_near_term_anchoring_is_preserved():
    """A 3pm pass with the same size gap: the 16:00 peak is only an hour away,
    so most of the offset must still land — this is the afternoon
    follow-reality-down behaviour the anchor exists for."""
    times, temps = _hours(*_CURVE)
    got = model._member_extreme(times, temps, _DAY, "high", _at(15),
                                observed=99.5, obs_now=98.0)
    # forecast 16:00 = 101.0, gap = 98.0 - fc(15:00 interp 100.0) = -2.0
    assert got < 100.0, f"near-term anchor was over-damped: {got}"


def test_decay_is_monotonic_in_lead():
    """The same offset must move a near hour more than a distant one."""
    times, temps = _hours(*_CURVE)
    near = model._member_extreme(times, temps, _DAY, "high", _at(15),
                                 observed=None, obs_now=98.0)
    far = model._member_extreme(times, temps, _DAY, "high", _at(9),
                                observed=None, obs_now=83.6)
    raw_peak = 101.0
    assert (raw_peak - near) > (raw_peak - far)


def test_zero_offset_is_a_no_op():
    """A member reading exactly its own forecast is unshifted at any lead."""
    times, temps = _hours(*_CURVE)
    got = model._member_extreme(times, temps, _DAY, "high", _at(9),
                                observed=86.0, obs_now=86.0)
    assert abs(got - 101.0) < 1e-9


def test_low_anchor_is_undecayed():
    """The low keeps the rigid 1:1 shift: its error is already unbiased and the
    front guard depends on distant anchored post-noon projections."""
    times, temps = _hours((6, 78.0), (12, 95.0), (18, 88.0), (23, 74.0))
    got = model._member_extreme(times, temps, _DAY, "low", _at(9),
                                observed=78.0, obs_now=None)
    # fc interpolated to 09:00 is 86.5; reading 84.5 is a -2.0 gap, which must
    # reach the 23:00 hour (14h out) at full strength: 74.0 -> 72.0.
    undecayed = model._member_extreme(times, temps, _DAY, "low", _at(9),
                                      observed=78.0, obs_now=84.5)
    assert got == 74.0
    assert abs(undecayed - 72.0) < 1e-6


def test_locked_high_still_pins_to_observed():
    """Decay must not touch the locked path — a passed peak is the answer."""
    times, temps = _hours(*_CURVE)
    got = model._member_extreme(times, temps, _DAY, "high", _at(18),
                                observed=101.4, obs_now=98.0, locked=True)
    assert got == 101.4


def test_decay_constant_is_configured():
    assert ANCHOR_DECAY_HOURS > 0
