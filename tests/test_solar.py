"""Dependency-free sunrise for KDFW."""
from datetime import date

from solar import sunrise
from config import LAT, LON


def test_kdfw_summer_sunrise():
    sr = sunrise(date(2026, 7, 2))
    assert sr.tzinfo is not None
    assert sr.date() == date(2026, 7, 2)
    # ~06:23 CDT (UTC-5). Allow a few minutes of algorithm slack.
    assert sr.utcoffset().total_seconds() == -5 * 3600          # CDT
    mins = sr.hour * 60 + sr.minute
    assert 6 * 60 + 18 <= mins <= 6 * 60 + 28                    # 06:18–06:28


def test_kdfw_winter_sunrise_is_cst():
    sr = sunrise(date(2026, 1, 15))
    assert sr.date() == date(2026, 1, 15)
    assert sr.utcoffset().total_seconds() == -6 * 3600          # CST (DST handled)
    mins = sr.hour * 60 + sr.minute
    assert 7 * 60 + 20 <= mins <= 7 * 60 + 40                    # ~07:30


def test_accepts_explicit_coords():
    # Same call with explicit KDFW coords matches the default-arg call.
    assert sunrise(date(2026, 7, 2), LAT, LON) == sunrise(date(2026, 7, 2))
