"""The Hourly page's 20-city registry — membership, ordering, and the
climate-day rule its CLI box is gated on."""
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import hourly_cities
import scan_cities


def test_covers_every_kalshi_screened_city():
    # The registry and the screen's coordinate table must not drift apart.
    screened = {scan_cities._SERIES_CITY[s] for s in scan_cities.CITY_POINTS}
    assert {c.key for c in hourly_cities.CITIES} == screened
    assert len(hourly_cities.CITIES) == 20


def test_modeled_cities_come_first_then_alphabetical():
    names = [c.name for c in hourly_cities.CITIES]
    assert names[:2] == ["Dallas", "Austin"]
    assert names[2:] == sorted(names[2:])


def test_dallas_and_austin_are_built_from_config():
    # Their rendered page must stay identical to today's, so every displayed
    # value comes from the StationConfig rather than a second hand-typed copy.
    for key, code in (("DAL", "KDFW"), ("AUS", "KAUS")):
        c = hourly_cities.city(key)
        s = config.station(code)
        assert (c.name, c.station, c.lat, c.lon) == (s.name, s.id, s.lat, s.lon)
        assert (c.timezone, c.climate_tz, c.cli_location) == (
            s.timezone, s.climate_tz, s.cli_location)
        assert c.modeled == code


def test_reference_cities_are_not_marked_modeled():
    assert hourly_cities.city("ATL").modeled is None


def test_reference_coordinates_come_from_scan_cities():
    assert (hourly_cities.city("ATL").lat,
            hourly_cities.city("ATL").lon) == scan_cities.coords_of("ATL")


def test_verified_station_ids_and_zones():
    # Verified live 2026-08-07 against api.weather.gov; the surprising ones.
    c = hourly_cities.city("CHI")
    assert (c.station, c.cli_location) == ("KORD", "ORD")
    assert hourly_cities.city("HOU").station == "KIAH"       # not Hobby
    assert hourly_cities.city("NYC").station == "KNYC"       # Central Park
    assert hourly_cities.city("PHX").timezone == "America/Phoenix"
    assert hourly_cities.city("MIA").timezone == "America/New_York"


def test_every_city_has_a_plausible_station_and_zone():
    for c in hourly_cities.CITIES:
        assert c.station.startswith("K") and len(c.station) == 4
        assert ZoneInfo(c.timezone)          # raises if the zone is bogus
        assert c.climate_tz.startswith("Etc/GMT+")
        assert c.cli_location.isupper() and len(c.cli_location) == 3


def test_label_names_the_station():
    assert hourly_cities.label("ATL") == "Atlanta (KATL)"


def test_keys_are_in_display_order_and_default_is_dallas():
    assert hourly_cities.keys()[:2] == ["DAL", "AUS"]
    assert hourly_cities.DEFAULT_KEY == "DAL"
    assert hourly_cities.city(hourly_cities.DEFAULT_KEY).name == "Dallas"


def test_unknown_key_falls_back_to_the_default():
    # A stale session-state key must not crash the page.
    assert hourly_cities.city("NOPE").key == hourly_cities.DEFAULT_KEY


def test_climate_day_uses_fixed_standard_time():
    # 00:30 local on a Pacific summer night is still the previous climate day:
    # PDT is UTC-7, so LST (UTC-8) reads 23:30 of the day before.
    c = hourly_cities.city("LAX")
    now = datetime(2026, 8, 7, 0, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert hourly_cities.climate_day(c, now).isoformat() == "2026-08-06"


def test_climate_day_is_the_clock_date_during_the_day():
    c = hourly_cities.city("LAX")
    now = datetime(2026, 8, 7, 14, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert hourly_cities.climate_day(c, now).isoformat() == "2026-08-07"
