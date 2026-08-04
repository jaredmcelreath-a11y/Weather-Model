from datetime import date

import screen_forecast as sf


def test_climate_day_comes_from_the_ticker():
    # Kalshi embeds the event date; close_time does NOT give it directly --
    # KXHIGHAUS-26AUG04-T97 closes 2026-08-05T05:59Z, which is Aug 5 in local
    # time and only Aug 4 in fixed standard time.
    assert sf.climate_day_of_ticker("KXHIGHAUS-26AUG04-T97") == date(2026, 8, 4)
    assert sf.climate_day_of_ticker("KXLOWTDEN-26AUG03-B72.5") == date(2026, 8, 3)


def test_an_unparseable_ticker_is_none():
    assert sf.climate_day_of_ticker("garbage") is None
    assert sf.climate_day_of_ticker("") is None
    assert sf.climate_day_of_ticker("KXHIGHAUS-26XXX99-T97") is None


def test_lst_offset_ignores_daylight_saving():
    assert sf.lst_offset_hours("America/Chicago") == -6      # CST, not CDT
    assert sf.lst_offset_hours("America/Denver") == -7
    assert sf.lst_offset_hours("America/New_York") == -5
    assert sf.lst_offset_hours("America/Phoenix") == -7      # never shifts


def test_daily_extremes_uses_only_the_targeted_climate_day():
    periods = [
        {"startTime": "2026-08-03T23:00:00-06:00", "temperature": 70},
        {"startTime": "2026-08-04T02:00:00-06:00", "temperature": 61},
        {"startTime": "2026-08-04T15:00:00-06:00", "temperature": 96},
        {"startTime": "2026-08-05T02:00:00-06:00", "temperature": 55},
    ]
    got = sf.daily_extremes(periods, date(2026, 8, 4), "America/Denver")
    assert got["high"] == 96
    assert got["low"] == 61          # 55 belongs to Aug 5, 70 to Aug 3


def test_daily_extremes_is_none_when_the_day_is_absent():
    periods = [{"startTime": "2026-08-03T23:00:00-06:00", "temperature": 70}]
    got = sf.daily_extremes(periods, date(2026, 8, 9), "America/Denver")
    assert got == {"high": None, "low": None}


def test_daily_extremes_skips_periods_with_no_temperature():
    periods = [
        {"startTime": "2026-08-04T15:00:00-06:00", "temperature": None},
        {"startTime": "2026-08-04T16:00:00-06:00", "temperature": 91},
    ]
    got = sf.daily_extremes(periods, date(2026, 8, 4), "America/Denver")
    assert got["high"] == 91 and got["low"] == 91


def test_fold_realized_corrects_a_day_already_in_progress():
    # The killer bug: for a day in progress the remaining forecast periods no
    # longer contain the extreme that already occurred. OKC's low of 65 had
    # passed, so the forecast-only low was this evening's 82.
    assert sf.fold_realized(82.0, [65.0, 70.0, 82.0], "low") == 65.0
    assert sf.fold_realized(94.0, [88.0, 96.0], "high") == 96.0


def test_fold_realized_keeps_the_forecast_when_it_is_more_extreme():
    assert sf.fold_realized(60.0, [70.0, 72.0], "low") == 60.0
    assert sf.fold_realized(99.0, [88.0], "high") == 99.0


def test_fold_realized_handles_missing_sides():
    assert sf.fold_realized(None, [65.0, 70.0], "low") == 65.0
    assert sf.fold_realized(82.0, [], "low") == 82.0
    assert sf.fold_realized(None, [], "low") is None
