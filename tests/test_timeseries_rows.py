"""Timeseries reductions: which feed a row came from, and the climate-day extremes."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import timeseries_view

VEGAS = ZoneInfo("America/Los_Angeles")


def _props(stamp, temp_c=None, dew_c=None, wind_kmh=None, wind_deg=None, raw=""):
    return {"timestamp": stamp,
            "temperature": {"value": temp_c},
            "dewpoint": {"value": dew_c},
            "windSpeed": {"value": wind_kmh},
            "windDirection": {"value": wind_deg},
            "rawMessage": raw}


def test_a_raw_metar_marks_the_row_hourly():
    # The :53 METAR carries the T group in TENTHS. Verified live at KLAS:
    # T03780111 alongside temperature 37.8.
    assert timeseries_view.is_hourly(
        _props("2026-08-16T21:56:00+00:00", 37.8,
               raw="KLAS 162156Z 22008KT 38/11 A3002 RMK AO2 T03780111"))


def test_an_empty_raw_message_marks_the_row_five_minute():
    # The 5-minute MADIS rows carry no raw text and are WHOLE degC -- 39, 38 --
    # which is why they must not be read as equally precise.
    assert not timeseries_view.is_hourly(_props("2026-08-16T23:25:00+00:00", 39))
    assert not timeseries_view.is_hourly({"timestamp": "x"})


def test_tenths_mark_a_metar_even_before_its_raw_text_arrives():
    # THE TRAP that shipped wrong the first time. `rawMessage` LAGS the numeric
    # fields by up to an hour -- measured 2026-08-16 across three stations, the
    # newest METAR had tenths and an empty rawMessage while the one an hour
    # older carried 69 bytes of raw text:
    #     KDFW 22:53 temp=38.9 rawlen=0
    #     KDFW 21:53 temp=38.9 rawlen=69
    # Keying off the raw text alone therefore mislabels the newest METAR, which
    # is the one row on this page anyone is actually watching.
    assert timeseries_view.is_hourly(_props("2026-08-16T22:53:00+00:00", 38.9))
    assert timeseries_view.is_hourly(_props("2026-08-16T22:52:00+00:00", 34.4))


def test_a_metar_landing_on_the_whole_degree_reads_as_five_minute():
    # Unresolvable and deliberately conservative: a METAR of exactly 38.0degC is
    # indistinguishable by value from the 5-minute row's whole-degC 38. Claiming
    # tenths it might not have is the error that costs money -- the same
    # on-grid reasoning screen_rules._reading_slack_f documents -- so an on-grid
    # reading is called the less precise thing unless raw text proves otherwise.
    assert not timeseries_view.is_hourly(_props("2026-08-16T22:53:00+00:00", 38.0))
    assert timeseries_view.is_hourly(
        _props("2026-08-16T22:53:00+00:00", 38.0, raw="KDFW 162253Z 16005KT"))


def test_calm_wind_is_not_reported_as_a_northerly():
    # Speed 0 with direction 0 is CALM. Rendering it "N 0" invents a compass
    # bearing the station never reported.
    assert timeseries_view._wind(0.0, "N") == "Calm"


def test_a_reading_converts_units_and_localises():
    # degC -> degF, km/h -> mph, degrees -> compass, UTC -> the city's zone.
    got = timeseries_view.reading(
        _props("2026-08-16T23:25:00+00:00", 39.0, 12.0, 16.1, 220), VEGAS)
    assert got["temp_f"] == 102.2
    assert got["dewpoint_f"] == 53.6
    assert got["wind_mph"] == 10.0
    assert got["wind_dir"] == "SW"
    assert got["time"].hour == 16                 # 23:25Z is 16:25 PDT
    assert got["hourly"] is False


def test_a_reading_without_a_temperature_is_dropped():
    # A row with no temperature has nothing this page exists to show.
    assert timeseries_view.reading(_props("2026-08-16T23:25:00+00:00"), VEGAS) is None


def test_compass_wraps_at_north():
    assert timeseries_view.compass(0) == "N"
    assert timeseries_view.compass(354) == "N"
    assert timeseries_view.compass(90) == "E"
    assert timeseries_view.compass(None) == ""


def _at(stamp_utc, temp_f):
    return {"time": datetime.fromisoformat(stamp_utc).astimezone(VEGAS),
            "temp_f": temp_f, "dewpoint_f": None, "wind_mph": None,
            "wind_dir": "", "raw": "", "hourly": False}


def test_extremes_cover_the_fixed_lst_day_not_the_local_day():
    # THE TRAP, the same one city_consensus documents. Las Vegas LST is UTC-8
    # all year, so the Aug 16 climate day runs 08:00Z Aug 16 to 08:00Z Aug 17.
    # The 08:30Z Aug 17 reading is Aug 17's, though it is 01:30 LOCAL on Aug 17
    # and would land on Aug 16 under any DST-aware local rule.
    rows = [_at("2026-08-17T08:30:00+00:00", 120.0),   # next climate day
            _at("2026-08-16T23:25:00+00:00", 102.2),
            _at("2026-08-16T13:00:00+00:00", 80.0)]
    got = timeseries_view.day_extremes(rows, date(2026, 8, 16), "Etc/GMT+8")
    assert got["high"][0] == 102.2                     # NOT 120.0
    assert got["low"][0] == 80.0


def test_extremes_report_when_each_happened():
    rows = [_at("2026-08-16T23:25:00+00:00", 102.2),
            _at("2026-08-16T13:00:00+00:00", 80.0)]
    got = timeseries_view.day_extremes(rows, date(2026, 8, 16), "Etc/GMT+8")
    assert got["high"][1].hour == 16                   # 23:25Z = 16:25 PDT
    assert got["low"][1].hour == 6


def test_extremes_are_none_when_the_day_has_no_readings():
    got = timeseries_view.day_extremes([], date(2026, 8, 16), "Etc/GMT+8")
    assert got == {"high": None, "low": None}


def test_table_rows_name_the_feed_each_row_came_from():
    rows = [{"time": datetime(2026, 8, 16, 16, 25, tzinfo=VEGAS),
             "temp_f": 102.2, "dewpoint_f": 53.6, "wind_mph": 10.0,
             "wind_dir": "SW", "raw": "KLAS 162156Z", "hourly": True},
            {"time": datetime(2026, 8, 16, 16, 20, tzinfo=VEGAS),
             "temp_f": 100.4, "dewpoint_f": None, "wind_mph": None,
             "wind_dir": "", "raw": "", "hourly": False}]
    got = timeseries_view.table_rows(rows)
    assert got[0]["Feed"] == "METAR"
    assert got[0]["Temp"] == "102.2°"
    assert got[0]["Wind"] == "SW 10"
    assert got[1]["Feed"] == "5-min"
    assert got[1]["Dew pt"] == "—"
    assert got[1]["Wind"] == "—"
    assert set(timeseries_view._COLUMNS) >= {"Time", "Temp", "Feed"}
