"""The one batched Open-Meteo request that feeds every city's consensus."""
import pytest

from sources import open_meteo_cities


def _capture():
    """A stub get() that records its params and returns a 2-location payload."""
    seen = {}

    def get(url, params=None, **kw):
        seen["url"] = url
        seen["params"] = params
        seen["kw"] = kw
        return [{"latitude": 1.0, "hourly": {"time": [0], "temperature_2m_gfs_seamless": [70.0]}},
                {"latitude": 2.0, "hourly": {"time": [0], "temperature_2m_gfs_seamless": [80.0]}}]

    return get, seen


def test_every_coordinate_goes_in_one_request():
    get, seen = _capture()
    out = open_meteo_cities.fetch([(1.0, -1.5), (2.0, -2.5)], models=["gfs_seamless"], get=get)
    assert len(out) == 2
    assert seen["params"]["latitude"] == "1.0,2.0"
    assert seen["params"]["longitude"] == "-1.5,-2.5"


def test_the_day_fold_is_ours_not_open_meteos():
    # Asking for daily aggregates would give us local-time-WITH-DST days, an
    # hour off the fixed-LST climate day in summer.
    get, seen = _capture()
    open_meteo_cities.fetch([(1.0, -1.5)], models=["gfs_seamless"], get=get)
    assert seen["params"]["hourly"] == "temperature_2m"
    assert "daily" not in seen["params"]
    assert seen["params"]["timeformat"] == "unixtime"


def test_a_single_coordinate_still_yields_a_list():
    # Open-Meteo answers ONE coordinate with a bare object and many with an
    # array. Callers must not have to care.
    def get(url, params=None, **kw):
        return {"latitude": 1.0, "hourly": {"time": [0], "temperature_2m_gfs_seamless": [70.0]}}

    out = open_meteo_cities.fetch([(1.0, -1.5)], models=["gfs_seamless"], get=get)
    assert isinstance(out, list) and len(out) == 1


def test_the_payload_reaches_back_far_enough_to_cover_a_running_climate_day():
    # Open-Meteo starts its payload at 00:00Z of the CURRENT GMT day. A climate
    # day is a fixed-LST window, so once GMT ticks past midnight most of a day
    # still in progress falls BEFORE the payload starts. Measured live on
    # 2026-08-10 at 01:30Z: Atlanta's Aug 9 climate day (05:00Z Aug 9 to 05:00Z
    # Aug 10) had 5 of its 24 hours in the payload, so the "model high" was the
    # max of five evening hours -- 81.5 against a realized 91.4.
    #
    # past_days=1 starts the payload a full day earlier, which covers every US
    # city: the furthest west is UTC-8, whose climate day begins 8h into the GMT
    # day, and the payload now begins 24h before that GMT day.
    get, seen = _capture()
    open_meteo_cities.fetch([(1.0, -1.5)], models=["gfs_seamless"], get=get)
    assert seen["params"]["past_days"] == 1
    assert seen["params"]["forecast_days"] == 3   # unchanged: today + two ahead


def test_no_coordinates_makes_no_request():
    def get(url, params=None, **kw):
        raise AssertionError("must not be called")

    assert open_meteo_cities.fetch([], get=get) == []
