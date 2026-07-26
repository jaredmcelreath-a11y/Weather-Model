"""Austin (KAUS) config values, verified against live NWS + Kalshi on 2026-07-26
(see docs/benchmarks/2026-07-26-austin-basis/FINDINGS.md)."""
import config


def test_kaus_settlement_values_verified():
    aus = config.station("KAUS")
    assert aus.id == "KAUS"              # settles on Austin-Bergstrom, not Camp Mabry
    assert aus.cli_location == "AUS"
    assert aus.climate_tz == "Etc/GMT+6"  # LST climate day, same as CLIDFW
    # lat/lon are the Bergstrom settlement station.
    assert 30.0 < aus.lat < 30.4 and -98.0 < aus.lon < -97.4


def test_kaus_kalshi_series_are_asymmetric():
    aus = config.station("KAUS")
    # Austin's high dropped the 'T'; the low kept it (verified against Kalshi).
    assert aus.kalshi_high_series == "KXHIGHAUS"
    assert aus.kalshi_low_series == "KXLOWTAUS"


def test_kdfw_kalshi_series_unchanged():
    dfw = config.station("KDFW")
    assert dfw.kalshi_high_series == "KXHIGHTDAL"
    assert dfw.kalshi_low_series == "KXLOWTDAL"


def test_kaus_convective_map_populated():
    aus = config.station("KAUS")
    m = aus.convective_counties
    assert m, "Austin convective map must be non-empty"
    # Travis County (the airport) is the metro anchor.
    assert any(v[1] == "metro" for v in m.values())
    # Values are (county_name, approach_direction) like KDFW's.
    assert all(isinstance(v, tuple) and len(v) == 2 for v in m.values())
    # Travis is present and tagged metro.
    assert m.get("TXC453") == ("Travis", "metro")


def test_cli_parser_accepts_both_office_time_formats():
    """CLIAUS (Austin/San Antonio) prints colon times; CLIDFW (Fort Worth) does
    not. One parser must handle both."""
    from datetime import datetime
    from sources import nws_cli

    aus = (
        "...THE AUSTIN BERGSTROM CLIMATE SUMMARY FOR JULY 26 2026...\n"
        "  MAXIMUM         78  12:20 AM 106    1954  97    -19\n"
        "  MINIMUM         73   5:59 AM  61    2019  73      0\n"
    )
    dfw = (
        "CLIMATE SUMMARY FOR JULY 25 2026...\n"
        "  MAXIMUM         98   519 PM 110    1954  97    -19\n"
        "  MINIMUM         80   649 AM  61    2019  73      0\n"
    )
    now = datetime(2026, 7, 26, 12, 0)
    ra = nws_cli.parse_cli(aus, now)
    assert ra and ra["high_f"] == 78 and ra["low_f"] == 73
    rd = nws_cli.parse_cli(dfw, now)
    assert rd and rd["high_f"] == 98 and rd["low_f"] == 80
