"""NWS CLIDFW daily climate report — parsing."""
from datetime import date, datetime, timezone

from sources import nws_cli

# Real CLIDFW product text captured 2026-07-20 (afternoon preliminary).
FIXTURE = """
000
CDUS44 KFWD 202141
CLIDFW

CLIMATE REPORT
NATIONAL WEATHER SERVICE FORT WORTH TX
441 PM CDT MON JUL 20 2026

...................................

...THE DALLAS/FORT WORTH CLIMATE SUMMARY FOR JULY 20 2026...
VALID AS OF 0400 PM LOCAL TIME.

CLIMATE NORMAL PERIOD 1991 TO 2020
CLIMATE RECORD PERIOD 1898 TO 2026


WEATHER ITEM   OBSERVED TIME   RECORD YEAR NORMAL DEPARTURE LAST
                VALUE   (LST)  VALUE       VALUE  FROM      YEAR
...................................................................
TEMPERATURE (F)
 TODAY
  MAXIMUM        100    254 PM 109    2022  96      4       95
  MINIMUM         80    615 AM  65    1920  76      4       80
  AVERAGE         90

PRECIPITATION (IN)
  TODAY            0.00          1.10 1920   0.05  -0.05     0.00
  MONTH TO DATE    1.36                      1.56  -0.20     1.52
"""

# A prior-day early-AM issuance reports the *previous* completed day.
FIXTURE_PRIOR_DAY = FIXTURE.replace("JULY 20 2026", "JULY 19 2026")

_ISSUED = datetime(2026, 7, 20, 21, 41, tzinfo=timezone.utc)


def test_parse_extracts_high_low_times_and_date():
    r = nws_cli.parse_cli(FIXTURE, _ISSUED)
    assert r["high_f"] == 100
    assert r["low_f"] == 80
    assert r["high_time"] == "254 PM"
    assert r["low_time"] == "615 AM"
    assert r["report_date"] == date(2026, 7, 20)


def test_parse_issued_is_localized():
    r = nws_cli.parse_cli(FIXTURE, _ISSUED)
    # 21:41 UTC == 16:41 local (America/Chicago, CDT)
    assert r["issued"].hour == 16
    assert r["issued"].tzinfo is not None


def test_parse_prior_day_report_carries_prior_date():
    r = nws_cli.parse_cli(FIXTURE_PRIOR_DAY, _ISSUED)
    assert r["report_date"] == date(2026, 7, 19)


def test_parse_malformed_returns_none():
    assert nws_cli.parse_cli("garbage with no fields", _ISSUED) is None
    assert nws_cli.parse_cli("", _ISSUED) is None
