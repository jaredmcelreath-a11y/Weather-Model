from datetime import date, datetime, timedelta, timezone

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


# ---- Storm chance ----------------------------------------------------------
# A gap is only as good as the forecast it is measured from, and a convective
# day is when that forecast is least reliable. Thunder-only POP over the hours
# that can still move the extreme.

def _p(start, temp, pop=None, short="Sunny"):
    period = {"startTime": start, "temperature": temp, "shortForecast": short}
    if pop is not None:
        period["probabilityOfPrecipitation"] = {"value": pop}
    return period


STORMY = "Chance Showers And Thunderstorms"
RAINY = "Chance Rain Showers"
DAY = date(2026, 8, 4)
TZ = "America/Chicago"


def _hour(h, temp, pop=None, short="Sunny"):
    """A period at hour `h` of 2026-08-04, Chicago standard time (-06:00)."""
    return _p(f"2026-08-04T{h:02d}:00:00-06:00", temp, pop, short)


def test_storm_chance_counts_thunder_hours_only():
    # 70% of rain is not a storm; the 45% thunder hour is the answer.
    periods = [_hour(13, 88, 70, RAINY), _hour(14, 90, 45, STORMY),
               _hour(15, 91, 20, STORMY)]
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    assert sf.storm_chance(periods, DAY, TZ, "high", now) == 45


def test_storm_chance_is_zero_when_no_hour_mentions_thunder():
    # Distinct from None: the window exists and is clean.
    periods = [_hour(13, 88, 70, RAINY), _hour(14, 90, 60, RAINY)]
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    assert sf.storm_chance(periods, DAY, TZ, "high", now) == 0


def test_storm_chance_ignores_hours_already_past():
    # An 80% storm at 13:00 is history at 18:00 UTC (12:00 CST).
    periods = [_hour(11, 85, 80, STORMY), _hour(13, 90, 30, STORMY)]
    now = datetime(2026, 8, 4, 18, tzinfo=timezone.utc)      # 12:00 LST
    assert sf.storm_chance(periods, DAY, TZ, "high", now) == 30


def test_a_highs_window_ends_at_the_forecast_peak():
    # Once the peak has passed a storm cannot RAISE the day's high, so the 90%
    # evening storm is irrelevant to a high row.
    periods = [_hour(14, 95, 10, STORMY),      # the peak
               _hour(21, 78, 90, STORMY)]      # after it: ignored
    now = datetime(2026, 8, 4, 17, tzinfo=timezone.utc)      # 11:00 LST
    assert sf.storm_chance(periods, DAY, TZ, "high", now) == 10


def test_a_lows_window_runs_to_the_end_of_the_day():
    # Evening convection CAN crash a low before midnight -- the whole reason
    # convective.py exists. The low is not the high run backwards.
    periods = [_hour(5, 74, 10, STORMY),       # the forecast minimum
               _hour(21, 78, 90, STORMY)]      # after it: still counts
    now = datetime(2026, 8, 4, 6, tzinfo=timezone.utc)       # 00:00 LST
    assert sf.storm_chance(periods, DAY, TZ, "low", now) == 90


def test_storm_chance_excludes_another_climate_day():
    # The neighbouring day's storms are not this bracket's problem.
    periods = [_p("2026-08-05T14:00:00-06:00", 90, 95, STORMY),
               _hour(14, 88, 25, STORMY)]
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    assert sf.storm_chance(periods, DAY, TZ, "high", now) == 25


def test_a_missing_pop_counts_as_zero_not_a_crash():
    periods = [_hour(14, 90, None, STORMY), _hour(15, 91, 35, STORMY)]
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    assert sf.storm_chance(periods, DAY, TZ, "high", now) == 35


def test_storm_chance_is_none_when_the_window_is_empty():
    # Nothing ahead that can still move the extreme.
    periods = [_hour(14, 90, 80, STORMY)]
    now = datetime(2026, 8, 5, 6, tzinfo=timezone.utc)       # day already over
    assert sf.storm_chance(periods, DAY, TZ, "high", now) is None


def test_storm_chance_is_none_for_a_day_with_no_forecast():
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    assert sf.storm_chance([], DAY, TZ, "high", now) is None


def test_storm_chance_matches_thunderstorms_case_insensitively():
    periods = [_hour(14, 90, 55, "Slight Chance thunderstorms")]
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    assert sf.storm_chance(periods, DAY, TZ, "high", now) == 55


def test_a_high_whose_peak_has_passed_has_no_window_left():
    # 18:00 LST, peak was at 14:00: no remaining hour can RAISE the day's high,
    # so there is no storm risk left that could move it. None, not 90.
    periods = [_hour(14, 95, 10, STORMY), _hour(21, 78, 90, STORMY)]
    now = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)       # 18:00 LST
    assert sf.storm_chance(periods, DAY, TZ, "high", now) is None


def test_a_low_whose_minimum_has_passed_still_has_a_window():
    # The mirror case, and the point of the asymmetry: the evening can still
    # take the low lower.
    periods = [_hour(5, 74, 10, STORMY), _hour(21, 78, 90, STORMY)]
    now = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)       # 18:00 LST
    assert sf.storm_chance(periods, DAY, TZ, "low", now) == 90


_MDT = timezone(timedelta(hours=-6))

_FC_PERIODS = [
    {"startTime": "2026-08-06T12:00:00-06:00", "temperature": 70},
    {"startTime": "2026-08-06T13:00:00-06:00", "temperature": 74},
]


def test_forecast_interpolates_between_the_bracketing_hours():
    # Snapping to the last whole hour makes this a step function that jumps at
    # the top of each hour while the observation anchor has not yet updated --
    # the sawtooth model.py:211 documents at KDFW.
    when = datetime(2026, 8, 6, 12, 30, tzinfo=_MDT)
    assert sf.forecast_at(_FC_PERIODS, when) == 72.0


def test_forecast_exactly_on_an_hour_is_that_hour():
    when = datetime(2026, 8, 6, 13, 0, tzinfo=_MDT)
    assert sf.forecast_at(_FC_PERIODS, when) == 74.0


def test_forecast_before_the_earliest_period_is_flat():
    # NWS hourly returns exactly one past hour, so a stale anchor at a slow
    # station can fall before the payload starts. Extrapolate flat rather than
    # abstain -- it is at most ~1 hour back.
    when = datetime(2026, 8, 6, 11, 15, tzinfo=_MDT)
    assert sf.forecast_at(_FC_PERIODS, when) == 70.0


def test_forecast_after_the_last_period_is_flat():
    when = datetime(2026, 8, 6, 15, 0, tzinfo=_MDT)
    assert sf.forecast_at(_FC_PERIODS, when) == 74.0


def test_forecast_with_no_usable_period_is_none():
    when = datetime(2026, 8, 6, 12, 30, tzinfo=_MDT)
    assert sf.forecast_at([], when) is None
    assert sf.forecast_at([{"startTime": "garbage", "temperature": 70}], when) is None
    assert sf.forecast_at(
        [{"startTime": "2026-08-06T12:00:00-06:00", "temperature": None}],
        when) is None


_ANCHOR_NOW = datetime(2026, 8, 6, 12, 0, tzinfo=_MDT)


def _reading(minutes_ago, temp):
    return (_ANCHOR_NOW - timedelta(minutes=minutes_ago), temp)


def test_the_anchor_averages_the_readings_in_the_window():
    # A single whole-degC reading jitters by up to 1.8F between samples, and the
    # drift shifts the whole remaining forecast 1:1 -- so a lone reading swings
    # the implied Ref while the temperature is flat.
    readings = [_reading(20, 68.0), _reading(10, 70.0), _reading(5, 72.0)]
    temp, at = sf.observed_anchor(readings, _ANCHOR_NOW)
    assert temp == 70.0
    # The timestamp is the mean of the contributing readings: 35/3 min ago.
    assert abs((_ANCHOR_NOW - at).total_seconds() - 700.0) < 1.0


def test_the_anchor_ignores_readings_outside_the_window():
    readings = [_reading(90, 50.0), _reading(10, 70.0)]
    temp, _ = sf.observed_anchor(readings, _ANCHOR_NOW)
    assert temp == 70.0


def test_a_slow_station_falls_back_to_its_newest_reading():
    # KDEN reports hourly and KNYC every ~31 min (measured 2026-08-06), so
    # nothing lands in the 30-minute window. Abstaining there would blank those
    # cities permanently rather than occasionally.
    temp, at = sf.observed_anchor([_reading(55, 66.0)], _ANCHOR_NOW)
    assert temp == 66.0
    assert (_ANCHOR_NOW - at).total_seconds() == 55 * 60


def test_an_anchor_past_the_age_cap_abstains():
    assert sf.observed_anchor([_reading(71, 66.0)], _ANCHOR_NOW) == (None, None)


def test_an_anchor_with_nothing_to_read_abstains():
    assert sf.observed_anchor([], _ANCHOR_NOW) == (None, None)
    assert sf.observed_anchor(None, _ANCHOR_NOW) == (None, None)
    assert sf.observed_anchor([(None, 70.0)], _ANCHOR_NOW) == (None, None)
    assert sf.observed_anchor([_reading(5, None)], _ANCHOR_NOW) == (None, None)


_DRIFT_PERIODS = [
    {"startTime": "2026-08-06T11:00:00-06:00", "temperature": 68},
    {"startTime": "2026-08-06T12:00:00-06:00", "temperature": 71},
    {"startTime": "2026-08-06T13:00:00-06:00", "temperature": 73},
]


# Readings 10 and 5 minutes back put the anchor at 11:52:30, NOT at `now` --
# which is the whole point of the anchor carrying its own timestamp. The ramp
# interpolates to 70.625 there, and every expectation below is measured from
# that, not from the 12:00 value of 71.
def test_a_station_warmer_than_the_forecast_drifts_positive():
    # Positive means the forecast is running COLD.
    readings = [_reading(10, 74.0), _reading(5, 74.0)]
    assert sf.forecast_drift(_DRIFT_PERIODS, readings, _ANCHOR_NOW) == 3.38


def test_a_forecast_running_hot_drifts_negative():
    # The San Francisco case of 2026-08-06: the grid ran ~3F above KSFO all
    # morning while Str quoted a 4F gap off the unadjusted number.
    readings = [_reading(10, 68.0), _reading(5, 68.0)]
    assert sf.forecast_drift(_DRIFT_PERIODS, readings, _ANCHOR_NOW) == -2.62


def test_drift_is_measured_against_the_anchors_own_hour():
    # A 55-minute-old reading compared against the forecast for NOW would
    # manufacture drift out of the diurnal ramp alone. At 11:05 the forecast is
    # 68.25, so a 68.25 reading is no drift at all -- against the 12:00 value of
    # 71 it would have read -2.75.
    assert sf.forecast_drift(_DRIFT_PERIODS, [_reading(55, 68.25)],
                             _ANCHOR_NOW) == 0.0


def test_drift_abstains_when_either_input_does():
    readings = [_reading(10, 68.0)]
    assert sf.forecast_drift(_DRIFT_PERIODS, [], _ANCHOR_NOW) is None
    assert sf.forecast_drift([], readings, _ANCHOR_NOW) is None


# ---- The forecast still AHEAD in a variable's window -----------------------

def _phx_period(hour, temp, forecast="Sunny"):
    return {"startTime": f"2026-08-09T{hour:02d}:00:00-07:00", "temperature": temp,
            "probabilityOfPrecipitation": {"value": 0}, "shortForecast": forecast}


_PHX_DAY = [_phx_period(h, t) for h, t in
            [(5, 93), (6, 93), (14, 110), (17, 110), (18, 109), (19, 107),
             (20, 103), (21, 101), (22, 99), (23, 97)]]
_PHX_NOW = datetime(2026, 8, 10, 0, 18, tzinfo=timezone.utc)     # 17:18 LST


def test_a_lows_remaining_extreme_is_the_coldest_hour_still_ahead():
    # The threat to a low bracket is the temperature FALLING, so what matters is
    # the minimum still to come -- 97 at 11pm, the last hour of the climate day.
    got = sf.remaining_extreme(_PHX_DAY, date(2026, 8, 9), "America/Phoenix",
                               "low", _PHX_NOW)
    assert got == 97.0


def test_a_lows_window_runs_to_midnight_not_to_the_peak():
    # An evening downdraft can still crash a low, which is why still_open does
    # not truncate for a low. The 5am readings are behind us and excluded by
    # `now`, not by the window.
    window = sf.still_open(
        sf._day_periods(_PHX_DAY, date(2026, 8, 9), "America/Phoenix"), "low")
    assert len(window) == len(_PHX_DAY)


def test_a_highs_remaining_extreme_is_the_hottest_hour_still_ahead():
    # Asked at noon, before the 2pm peak: 110 is still to come.
    noon = datetime(2026, 8, 9, 19, 0, tzinfo=timezone.utc)      # 12:00 LST
    got = sf.remaining_extreme(_PHX_DAY, date(2026, 8, 9), "America/Phoenix",
                               "high", noon)
    assert got == 110.0


def test_a_high_after_its_peak_has_no_remaining_window():
    # still_open truncates a high at its PEAK, so by 17:18 LST -- with the 2pm
    # peak behind us -- nothing left can move it. The 7pm and 9pm periods are
    # dropped by the truncation, not by the clock: no later hour can raise a
    # high that has already happened.
    assert sf.remaining_extreme(_PHX_DAY, date(2026, 8, 9), "America/Phoenix",
                                "high", _PHX_NOW) is None


def test_remaining_extreme_of_a_day_with_no_periods_is_none():
    assert sf.remaining_extreme([], date(2026, 8, 9), "America/Phoenix",
                                "low", _PHX_NOW) is None
