"""Tests for the Kalshi CLI settlement basis (Part A): CLI truth fetch parsing,
the calibrated settlement offset, and the model's settle_offset shift."""

from datetime import date

from sources.station_history import _parse_daily

SAMPLE_CSV = (
    "station,day,max_temp_f,min_temp_f,precip_in\n"
    "DFW,2026-06-08,95.0,78.0,0.0\n"
    "DFW,2026-06-09,None,77.0,0.0\n"      # missing max -> skipped
    "DFW,2026-06-10,94.0,M,0.0\n"          # missing min -> skipped
    "DFW,2026-06-11,93.0,79.0,0.0\n"
)


def test_parse_daily_maps_day_to_high_low():
    out = _parse_daily(SAMPLE_CSV)
    assert out[date(2026, 6, 8)] == (95.0, 78.0)
    assert out[date(2026, 6, 11)] == (93.0, 79.0)


def test_parse_daily_skips_missing_rows():
    out = _parse_daily(SAMPLE_CSV)
    assert date(2026, 6, 9) not in out   # None max
    assert date(2026, 6, 10) not in out  # M min
    assert len(out) == 2
