from datetime import datetime
from zoneinfo import ZoneInfo

import betting_log
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _at(h, m):
    return datetime(2026, 7, 3, h, m, tzinfo=_TZ)


def test_current_slot_exact_match():
    assert betting_log.current_slot(_at(15, 30)) == "15:30"


def test_current_slot_within_tolerance():
    assert betting_log.current_slot(_at(15, 4)) == "15:00"    # +4 min
    assert betting_log.current_slot(_at(16, 24)) == "16:30"   # -6 min


def test_current_slot_outside_tolerance_is_none():
    assert betting_log.current_slot(_at(15, 12)) is None      # 12 min off any slot


def test_current_slot_all_five_slots_defined():
    assert betting_log.SLOTS == ["15:00", "15:30", "16:00", "16:30", "17:00"]
