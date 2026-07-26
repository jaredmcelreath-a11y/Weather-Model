import config


def test_kdfw_entry_matches_legacy_constants():
    dfw = config.station("KDFW")
    assert dfw.id == "KDFW"
    assert dfw.lat == 32.90 and dfw.lon == -97.04
    assert dfw.timezone == "America/Chicago"
    assert dfw.climate_tz == "Etc/GMT+6"
    assert dfw.cli_location == "DFW"
    assert dfw.bin_low == -10 and dfw.bin_high == 115
    # Convective map is the real KDFW geography (non-empty).
    assert dfw.convective_counties["TXC113"] == ("Dallas", "metro")


def test_bare_aliases_still_point_at_kdfw():
    assert config.STATION_ID == config.station("KDFW").id
    assert config.LAT == config.station("KDFW").lat
    assert config.CONVECTIVE_UPSTREAM_UGC == tuple(config.station("KDFW").convective_counties)


def test_default_and_lookup():
    assert config.DEFAULT_STATION == "KDFW"
    assert config.station() is config.station("KDFW")
    assert config.station("").code == "KDFW"
    assert "KAUS" in config.STATION_CODES


def test_kaus_entry_present():
    aus = config.station("KAUS")
    assert aus.id == "KAUS"
    assert aus.cli_location == "AUS"
    assert aus.lat == 30.1975 and aus.lon == -97.6664
    # Convective map populated in Plan 2 Task 2 (was empty at Plan 1); the
    # detailed map is asserted in test_austin_config.
    assert aus.convective_counties


def test_unknown_station_raises():
    import pytest
    with pytest.raises(KeyError):
        config.station("KXXX")
