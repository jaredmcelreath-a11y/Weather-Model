import scan_cities

_POINTS = {"properties": {
    "gridId": "BOU", "gridX": 74, "gridY": 66,
    "timeZone": "America/Denver",
    "forecastHourly": "https://api.weather.gov/gridpoints/BOU/74,66/forecast/hourly",
    "observationStations": "https://api.weather.gov/gridpoints/BOU/74,66/stations",
}}

_STATIONS = {"features": [
    {"properties": {"stationIdentifier": "KDEN"}},
    {"properties": {"stationIdentifier": "KCFO"}},
]}


def test_every_mapped_series_is_a_high_or_low_ticker():
    assert scan_cities.CITY_POINTS
    for s in scan_cities.CITY_POINTS:
        assert s.startswith("KXHIGH") or s.startswith("KXLOW")


def test_the_table_covers_both_variables_for_denver():
    assert scan_cities.point_for("KXHIGHDEN") is not None
    assert scan_cities.point_for("KXLOWTDEN") is not None


def test_high_and_low_of_one_city_share_a_coordinate():
    assert scan_cities.point_for("KXHIGHDEN") == scan_cities.point_for("KXLOWTDEN")


def test_an_unmapped_series_is_none():
    assert scan_cities.point_for("KXHIGHNOWHERE") is None


def test_resolve_pulls_timezone_and_urls_from_the_points_payload():
    got = scan_cities.resolve(39.8561, -104.6737, fetch=lambda url: _POINTS)
    assert got["timezone"] == "America/Denver"
    assert got["forecast_hourly"].endswith("/forecast/hourly")
    assert got["stations_url"].endswith("/stations")


def test_station_for_takes_the_nearest_station():
    got = scan_cities.station_for("u", fetch=lambda url: _STATIONS)
    assert got == "KDEN"


def test_station_for_returns_none_when_there_are_no_stations():
    assert scan_cities.station_for("u", fetch=lambda url: {"features": []}) is None
