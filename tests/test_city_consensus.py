"""The consensus reduction: fold onto an LST climate day, then average models."""
from datetime import date, datetime, timedelta, timezone

import city_consensus


def _ts(y, m, d, hour):
    """Unix seconds for a UTC wall time."""
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


def test_the_extreme_covers_the_lst_day_not_the_utc_day():
    # Denver LST is UTC-7 all year. The Aug 7 climate day therefore runs
    # 07:00Z Aug 7 to 07:00Z Aug 8. The 90 at 06:00Z Aug 7 belongs to Aug 6 and
    # must NOT become Aug 7's high.
    times = [_ts(2026, 8, 7, 6), _ts(2026, 8, 7, 12), _ts(2026, 8, 8, 2)]
    temps = [90.0, 80.0, 70.0]
    got = city_consensus.series_extreme(times, temps, date(2026, 8, 7), -7)
    assert got == {"high": 80.0, "low": 70.0}


def test_a_daylight_saving_local_day_would_have_been_an_hour_wrong():
    # THE TRAP, in the direction it actually bites. Denver's Aug 7 climate day
    # runs to 07:00Z Aug 8, so the 55 at 06:00Z Aug 8 is the day's low. In LOCAL
    # time that instant is midnight on Aug 8 (MDT is UTC-6 in August), so
    # Open-Meteo's own daily aggregate files it under Aug 8 and reports Aug 7's
    # low as 70 -- a whole degree-hour of the wrong day, every summer.
    times = [_ts(2026, 8, 8, 2), _ts(2026, 8, 8, 6)]
    temps = [70.0, 55.0]
    got = city_consensus.series_extreme(times, temps, date(2026, 8, 7), -7)
    assert got["low"] == 55.0          # NOT 70.0, which local-with-DST gives


def test_the_lst_day_ends_where_it_should():
    # One hour later -- 07:00Z Aug 8 -- and the reading belongs to Aug 8.
    got = city_consensus.series_extreme([_ts(2026, 8, 8, 7)], [55.0],
                                        date(2026, 8, 7), -7)
    assert got["low"] is None


def test_nulls_are_skipped_not_counted_as_zero():
    times = [_ts(2026, 8, 7, 12), _ts(2026, 8, 7, 13)]
    got = city_consensus.series_extreme(times, [None, 80.0], date(2026, 8, 7), -7)
    assert got == {"high": 80.0, "low": 80.0}


def test_a_day_with_no_readings_has_no_extreme():
    got = city_consensus.series_extreme([_ts(2026, 8, 9, 12)], [80.0],
                                        date(2026, 8, 7), -7)
    assert got == {"high": None, "low": None}


def test_model_extremes_are_keyed_by_model_not_by_api_column():
    hourly = {"time": [_ts(2026, 8, 7, 12), _ts(2026, 8, 7, 18)],
              "temperature_2m_gfs_seamless": [80.0, 92.0],
              "temperature_2m_gfs_hrrr": [81.0, 93.0]}
    got = city_consensus.model_extremes(hourly, date(2026, 8, 7), -7)
    assert got["gfs_seamless"]["high"] == 92.0
    assert got["gfs_hrrr"]["high"] == 93.0


def test_a_model_that_returned_only_nulls_is_absent():
    # HRRR past its 48-hour range, or a model outage.
    hourly = {"time": [_ts(2026, 8, 7, 12)],
              "temperature_2m_gfs_seamless": [80.0],
              "temperature_2m_gfs_hrrr": [None]}
    got = city_consensus.model_extremes(hourly, date(2026, 8, 7), -7)
    assert "gfs_hrrr" not in got


def test_consensus_is_the_plain_mean_and_the_full_spread():
    got = city_consensus.consensus([92.0, 91.5, 92.8, 92.9, 91.3])
    assert got["value"] == 92.1
    assert got["spread"] == 1.6
    assert got["n"] == 5


def test_consensus_needs_at_least_three_models():
    # Two models agreeing is not agreement, and the spread of two is a range,
    # not a distribution.
    assert city_consensus.consensus([92.0, 91.0]) is None
    assert city_consensus.consensus([]) is None


def test_consensus_survives_a_missing_model():
    # Routine for tomorrow, where HRRR does not reach.
    got = city_consensus.consensus([92.0, 91.5, 92.8, 92.9])
    assert got["n"] == 4


def test_consensus_ignores_nones_in_the_list():
    got = city_consensus.consensus([92.0, None, 91.5, 92.8, 92.9])
    assert got["n"] == 4
