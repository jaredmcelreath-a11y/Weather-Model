"""Unit tests for the settlement / day-window logic — the contract everything
else depends on. Uses synthetic series so the windowing and rounding are tested
independently of any live data."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import settlement as S
from config import TIMEZONE

TZ = ZoneInfo(TIMEZONE)
DAY = date(2025, 1, 15)

LST = ZoneInfo("Etc/GMT+6")


def test_local_day_bounds_is_lst_not_clock():
    # Summer: the settlement window starts at 00:00 LST = 01:00 CDT, one hour
    # after clock midnight — this is the CLIDFW climate day (verified May 2026).
    summer = date(2026, 7, 14)
    start, end = S.local_day_bounds(summer)
    assert start == datetime(2026, 7, 14, tzinfo=LST)
    assert start.astimezone(TZ).hour == 1          # 01:00 CDT, not 00:00
    assert (end - start) == timedelta(days=1)       # always exactly 24h (no DST)


def test_local_day_bounds_winter_matches_clock():
    # Winter: LST == CST == the old America/Chicago clock window, byte-identical.
    winter = date(2026, 1, 14)
    start, end = S.local_day_bounds(winter)
    assert start == datetime(2026, 1, 14, tzinfo=TZ)   # same absolute instant


def test_post_clock_midnight_reading_settles_prior_day():
    # The May 26 2026 pattern: a min recorded 00:30 CDT the NEXT clock day still
    # belongs to THIS settlement day (window ends 01:00 CDT next day). The old
    # clock window dropped it; the LST window keeps it.
    summer = date(2026, 7, 14)
    start, _ = S.local_day_bounds(summer)
    # a warm afternoon plus a cold reading at 00:30 CDT the next clock day
    times = [datetime(2026, 7, 14, 15, tzinfo=TZ),
             datetime(2026, 7, 15, 0, 30, tzinfo=TZ)]
    temps = [95.0, 70.0]
    hi, lo = S.day_high_low(times, temps, summer)
    assert lo == 70    # the post-midnight reading settles this day
    assert hi == 95


def _series(pairs):
    """pairs: list of (hour_offset_from_midnight, temp) -> (times, temps)."""
    start = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    times = [start + timedelta(hours=h) for h, _ in pairs]
    temps = [t for _, t in pairs]
    return times, temps


def test_high_low_within_day():
    times, temps = _series([(3, 78.4), (15, 99.6), (23, 80.0)])
    hi, lo = S.day_high_low(times, temps, DAY)
    assert hi == 100  # 99.6 rounds to 100
    assert lo == 78   # 78.4 rounds to 78


def _minute_series(values):
    """values: list of (minute_offset, temp) from midnight -> (times, temps)."""
    start = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    times = [start + timedelta(minutes=m) for m, _ in values]
    temps = [t for _, t in values]
    return times, temps


def test_robust_extreme_rejects_lone_spike():
    # The 5-min feed occasionally reports a single reading a whole degC off. A
    # genuine peak persists across >=2 readings; a lone spike must be discarded.
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)
    vals = [(m, 96.8) for m in range(0, 180, 5)]      # steady 96.8 all afternoon
    vals[10] = (vals[10][0], 98.6)                     # one spurious 37C spike
    times, temps = _minute_series(vals)

    raw_max, _ = S.observed_so_far(times, temps, DAY, now)
    robust_max, _ = S.observed_so_far_robust(times, temps, DAY, now)

    assert raw_max == 98.6           # the naive max takes the spike
    assert robust_max == 96.8        # the robust max rejects it


def test_robust_extreme_min_support_1_trusts_lone_spike():
    # With min_support=1 (used for the HIGH, since Kalshi settles on the raw CLI max),
    # a lone real spike IS trusted — a brief 5-min peak is what settles.
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)
    vals = [(m, 98.0) for m in range(0, 180, 5)]
    vals[10] = (vals[10][0], 100.4)                    # a single 5-min peak to 100.4
    times, temps = _minute_series(vals)

    robust_max, _ = S.observed_so_far_robust(times, temps, DAY, now)              # default (2)
    lone_max, _ = S.observed_so_far_robust(times, temps, DAY, now, min_support=1)
    assert robust_max == 98.0            # corroboration guard drops the lone spike
    assert lone_max == 100.4             # min_support=1 keeps it (raw max)


def test_robust_extreme_keeps_corroborated_peak():
    # A real peak — seen in 3 consecutive readings — must survive the filter.
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)
    vals = [(m, 96.8) for m in range(0, 180, 5)]
    for i in (10, 11, 12):
        vals[i] = (vals[i][0], 98.6)                   # a sustained 15-min peak
    times, temps = _minute_series(vals)

    robust_max, _ = S.observed_so_far_robust(times, temps, DAY, now)
    assert robust_max == 98.6


def test_excludes_other_days():
    # A scorching reading at the next midnight must NOT count for DAY.
    start = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    times = [start + timedelta(hours=12), start + timedelta(days=1)]  # noon, next 00:00
    temps = [95.0, 110.0]
    hi, _ = S.day_high_low(times, temps, DAY)
    assert hi == 95  # 110 at next-day midnight excluded


def test_empty_returns_none():
    assert S.day_high_low([], [], DAY) == (None, None)


def test_observed_so_far_respects_now():
    times, temps = _series([(6, 70.0), (10, 85.0), (16, 99.0)])
    now = datetime(DAY.year, DAY.month, DAY.day, 11, tzinfo=TZ)  # before the 16:00 peak
    mx, mn = S.observed_so_far(times, temps, DAY, now)
    assert mx == 85.0  # afternoon peak not yet observed
    assert mn == 70.0


def test_bin_for_temp():
    assert S.bin_for_temp(95.4) == "95"
    assert S.bin_for_temp(95.6) == "96"
    assert S.bin_for_temp(40) == f"<= {S.BIN_LOW}"
    assert S.bin_for_temp(130) == f">= {S.BIN_HIGH}"


def test_round_half_up_matches_nws_not_bankers():
    # Python's built-in round() would send 90.5 -> 90 (banker's); NWS/WU send it up.
    assert S.round_half_up(90.5) == 91
    assert S.round_half_up(91.5) == 92
    assert S.round_half_up(89.49) == 89
    assert S.round_half_up(89.6) == 90  # so 89.6 settles as 90, not "greater than 90"


def test_greater_than_uses_whole_degree():
    # "Greater than 90" needs the rounded high to be 91+, so an 89.6 (=>90) loses.
    import model
    probs = {"89": 0.2, "90": 0.5, "91": 0.2, "92": 0.1}
    assert abs(model.prob_greater_than(probs, 90) - 0.3) < 1e-9   # 91 + 92
    assert abs(model.prob_at_least(probs, 90) - 0.8) < 1e-9       # 90 or hotter
