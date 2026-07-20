from datetime import date, datetime
from zoneinfo import ZoneInfo

import settlement
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def test_climate_day_is_clock_day_during_the_day():
    # Midday CDT: the running climate day is the clock day.
    assert settlement.climate_day_of(datetime(2026, 7, 20, 12, 0, tzinfo=_TZ)) \
        == date(2026, 7, 20)


def test_climate_day_lags_in_the_final_summer_hour():
    # 00:30 CDT July 20 is still inside July 19's climate day (ends 01:00 CDT).
    assert settlement.climate_day_of(datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)) \
        == date(2026, 7, 19)


def test_climate_day_rolls_at_the_lst_boundary():
    assert settlement.climate_day_of(datetime(2026, 7, 20, 1, 0, tzinfo=_TZ)) \
        == date(2026, 7, 20)


def test_open_prior_day_in_the_final_summer_hour():
    assert settlement.open_prior_day(datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)) \
        == date(2026, 7, 19)
    assert settlement.open_prior_day(datetime(2026, 7, 20, 0, 0, tzinfo=_TZ)) \
        == date(2026, 7, 19)


def test_open_prior_day_closes_at_the_boundary_exactly():
    # end is exclusive: at 01:00 CDT July 19 has settled.
    assert settlement.open_prior_day(datetime(2026, 7, 20, 1, 0, tzinfo=_TZ)) is None
    assert settlement.open_prior_day(datetime(2026, 7, 20, 1, 1, tzinfo=_TZ)) is None


def test_open_prior_day_none_during_the_day():
    assert settlement.open_prior_day(datetime(2026, 7, 20, 12, 0, tzinfo=_TZ)) is None
    assert settlement.open_prior_day(datetime(2026, 7, 20, 23, 30, tzinfo=_TZ)) is None


def test_winter_has_no_open_prior_hour():
    # In CST the climate day coincides with clock midnight, so the gap the
    # last-hour trade lives in does not exist.
    for hour in (23, 0, 1):
        d = date(2026, 1, 6) if hour != 23 else date(2026, 1, 5)
        assert settlement.open_prior_day(datetime(d.year, d.month, d.day, hour, 30,
                                                  tzinfo=_TZ)) is None


def test_winter_climate_day_matches_clock_day():
    assert settlement.climate_day_of(datetime(2026, 1, 5, 23, 30, tzinfo=_TZ)) \
        == date(2026, 1, 5)
    assert settlement.climate_day_of(datetime(2026, 1, 6, 0, 30, tzinfo=_TZ)) \
        == date(2026, 1, 6)
