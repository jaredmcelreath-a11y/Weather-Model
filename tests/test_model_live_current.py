"""Which reading the dashboard shows as 'Current Temp'.

Three feeds can supply it, at different cadences and precisions:
  - the routine :53 METAR via tgftp -- tenths of °C, published ~2 min after
  - the 5-minute MADIS feed -- whole °C, ~20 min late
  - the hourly series -- the fallback when neither continuous feed is present

Newest wins, because "current" is a claim about now. Ties go to the METAR,
which is the finer of the two.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import model

TZ = ZoneInfo("America/Chicago")


def _at(hour, minute):
    return datetime(2026, 8, 16, hour, minute, tzinfo=TZ)


_HOURLY = {"temp": 100.0, "time": "2026-08-16T15:53-05:00"}


def test_the_metar_wins_while_the_5_minute_feed_is_still_behind():
    # The live 2026-08-16 KAUS case: at 17:05 the 5-minute feed's newest was
    # 16:50 reading 38C ("100"), while the :53 METAR already had 38.9C.
    cont = ([_at(16, 50)], [100.4])
    got = model.live_current(cont, _HOURLY, (_at(16, 53), 102.02))
    assert got == {"temp": 102.0, "time": "2026-08-16T16:53-05:00"}


def test_the_5_minute_feed_wins_once_it_has_caught_up_past_the_metar():
    # Later in the hour the continuous feed passes the :53 report. Showing the
    # older METAR then would reintroduce the staleness this feed was added to
    # remove.
    cont = ([_at(17, 20)], [103.1])
    got = model.live_current(cont, _HOURLY, (_at(16, 53), 102.02))
    assert got == {"temp": 103.1, "time": "2026-08-16T17:20-05:00"}


def test_a_tie_goes_to_the_metar_because_it_is_finer():
    # Same instant, whole °C against tenths: 39C reads 102.2, the T-group 102.02.
    cont = ([_at(16, 53)], [102.2])
    got = model.live_current(cont, _HOURLY, (_at(16, 53), 102.02))
    assert got["temp"] == 102.0


def test_a_dead_metar_feed_leaves_the_5_minute_reading_untouched():
    cont = ([_at(16, 50)], [100.4])
    got = model.live_current(cont, _HOURLY, None)
    assert got == {"temp": 100.4, "time": "2026-08-16T16:50-05:00"}


def test_the_metar_alone_still_beats_the_hourly_fallback():
    got = model.live_current(None, _HOURLY, (_at(16, 53), 102.02))
    assert got == {"temp": 102.0, "time": "2026-08-16T16:53-05:00"}


def test_no_continuous_feed_at_all_falls_back_to_the_hourly_reading():
    assert model.live_current(None, _HOURLY, None) == _HOURLY


def test_nothing_anywhere_is_none_rather_than_a_crash():
    assert model.live_current(None, None, None) is None


# ---- The routine :53 box -----------------------------------------------------
#
# `current_hourly` is the precise hourly reading shown beside the live one. It
# has always come from the api.weather.gov hourly series, which carries the
# right value ~20 minutes late. tgftp carries the SAME observation ~2 minutes
# after it happens, so the box can stop waiting for the slow copy.

def test_the_routine_box_takes_the_metar_before_the_hourly_series_has_it():
    # 16:53 measured live: tgftp had 102.02 at 16:55 while the hourly series
    # was still showing 15:53. This is the 18 minutes the feed buys.
    got = model.live_hourly([_at(15, 53)], [100.0], (_at(16, 53), 102.02))
    assert got == {"temp": 102.0, "time": "2026-08-16T16:53-05:00"}


def test_the_routine_box_keeps_the_hourly_series_when_it_is_ahead():
    # tgftp serves only the LATEST report; after a gap the series can be newer.
    got = model.live_hourly([_at(17, 53)], [99.0], (_at(16, 53), 102.02))
    assert got == {"temp": 99.0, "time": "2026-08-16T17:53-05:00"}


def test_the_routine_box_survives_a_dead_metar_feed():
    got = model.live_hourly([_at(16, 53)], [102.0], None)
    assert got == {"temp": 102.0, "time": "2026-08-16T16:53-05:00"}


def test_the_routine_box_works_from_the_metar_alone():
    # An NWS outage empties the hourly series; the direct feed still reports.
    got = model.live_hourly([], [], (_at(16, 53), 102.02))
    assert got == {"temp": 102.0, "time": "2026-08-16T16:53-05:00"}


def test_the_routine_box_is_none_when_nothing_reports():
    assert model.live_hourly([], [], None) is None
