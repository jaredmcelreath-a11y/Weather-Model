"""The extremes caption: the settling number, and the honesty about precision."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import timeseries_view

VEGAS = ZoneInfo("America/Los_Angeles")


def test_the_caption_reports_both_extremes_with_their_times():
    got = timeseries_view.extreme_caption(
        {"high": (102.2, datetime(2026, 8, 16, 16, 25, tzinfo=VEGAS)),
         "low": (80.0, datetime(2026, 8, 16, 6, 0, tzinfo=VEGAS))})
    assert "102.2" in got and "4:25 PM" in got
    assert "80.0" in got and "6:00 AM" in got


def test_a_missing_extreme_reads_as_nothing_yet_not_as_a_number():
    got = timeseries_view.extreme_caption({"high": None, "low": None})
    assert "—" in got


def test_one_extreme_present_does_not_invent_the_other():
    got = timeseries_view.extreme_caption(
        {"high": (102.2, datetime(2026, 8, 16, 16, 25, tzinfo=VEGAS)), "low": None})
    assert "102.2" in got
    assert "None" not in got
