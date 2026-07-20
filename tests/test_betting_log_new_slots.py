from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import betting_log
import settlement
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_TZ)


# --- evening day-ahead slots -------------------------------------------------

def test_evening_slots_match_fixed_clock_times():
    assert betting_log.current_slot(_at(2026, 7, 20, 21, 0)) == "eve-21:00"
    assert betting_log.current_slot(_at(2026, 7, 20, 22, 0)) == "eve-22:00"
    assert betting_log.current_slot(_at(2026, 7, 20, 23, 0)) == "eve-23:00"


def test_evening_slots_honor_tolerance():
    assert betting_log.current_slot(_at(2026, 7, 20, 20, 53)) == "eve-21:00"   # -7
    assert betting_log.current_slot(_at(2026, 7, 20, 21, 8)) == "eve-21:00"    # +8
    assert betting_log.current_slot(_at(2026, 7, 20, 21, 9)) is None           # +9


def test_evening_slots_target_tomorrow():
    now = _at(2026, 7, 20, 22, 0)
    assert betting_log.slot_target_day("eve-22:00", now) == date(2026, 7, 21)


# --- close slots, summer -----------------------------------------------------

def test_close_slots_land_after_clock_midnight_in_summer():
    # July 19's climate day ends 01:00 CDT July 20 -> close-45 = 00:15, close-15 = 00:45.
    assert betting_log.current_slot(_at(2026, 7, 20, 0, 15)) == "close-45"
    assert betting_log.current_slot(_at(2026, 7, 20, 0, 45)) == "close-15"


def test_close_slots_target_the_ending_climate_day_in_summer():
    assert betting_log.slot_target_day("close-45", _at(2026, 7, 20, 0, 15)) \
        == date(2026, 7, 19)
    assert betting_log.slot_target_day("close-15", _at(2026, 7, 20, 0, 45)) \
        == date(2026, 7, 19)


def test_close_slot_tolerance_stays_inside_the_climate_day():
    # +8 on close-15 is 00:53 CDT — still July 19's day (ends 01:00).
    assert betting_log.current_slot(_at(2026, 7, 20, 0, 53)) == "close-15"
    assert betting_log.slot_target_day("close-15", _at(2026, 7, 20, 0, 53)) \
        == date(2026, 7, 19)


# --- close slots, winter -----------------------------------------------------

def test_close_slots_land_before_clock_midnight_in_winter():
    # Jan 5's climate day ends 00:00 CST Jan 6 -> close-45 = 23:15, close-15 = 23:45.
    assert betting_log.current_slot(_at(2026, 1, 5, 23, 15)) == "close-45"
    assert betting_log.current_slot(_at(2026, 1, 5, 23, 45)) == "close-15"


def test_close_slots_target_clock_today_in_winter():
    assert betting_log.slot_target_day("close-45", _at(2026, 1, 5, 23, 15)) \
        == date(2026, 1, 5)


def test_no_close_slot_just_after_winter_midnight():
    assert betting_log.current_slot(_at(2026, 1, 6, 0, 20)) is None


# --- DST transition days -----------------------------------------------------

def test_close_slots_resolve_on_both_dst_transition_days():
    # Spring forward 2026-03-08, fall back 2026-11-01. The slot must still sit
    # exactly 45/15 min before that climate day's end, whatever the clock did.
    for day in (date(2026, 3, 8), date(2026, 11, 1)):
        end = settlement.local_day_bounds(day)[1]
        for label, off in betting_log.CLOSE_SLOT_OFFSETS:
            moment = (end + timedelta(minutes=off)).astimezone(_TZ)
            assert betting_log.current_slot(moment) == label
            assert betting_log.slot_target_day(label, moment) == day


# --- registry ----------------------------------------------------------------

def test_slot_registry_includes_both_new_families():
    assert betting_log.EVENING_SLOTS == ["eve-21:00", "eve-22:00", "eve-23:00"]
    assert betting_log.CLOSE_SLOTS == ["close-45", "close-15"]
    for s in betting_log.EVENING_SLOTS + betting_log.CLOSE_SLOTS:
        assert s in betting_log.SLOTS
        assert betting_log.SLOT_VARS[s] == ("high", "low")


def test_existing_slots_still_target_clock_today():
    now = _at(2026, 7, 20, 15, 30)
    assert betting_log.slot_target_day("15:30", now) == date(2026, 7, 20)
    assert betting_log.slot_target_day("sr", now) == date(2026, 7, 20)
