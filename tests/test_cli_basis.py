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


from calibration import _settlement_offset


def test_settlement_offset_means_the_cli_minus_hourly_gap():
    cli = {date(2026, 6, 8): (95.0, 78.0), date(2026, 6, 9): (94.0, 77.0)}
    hourly = {date(2026, 6, 8): (94.0, 78.0), date(2026, 6, 9): (93.0, 79.0)}
    off = _settlement_offset(cli, hourly)
    assert off["high"] == 1.0    # (1 + 1) / 2
    assert off["low"] == -1.0    # (0 + -2) / 2
    assert off["n_days"] == 2


def test_settlement_offset_zero_when_no_overlap():
    off = _settlement_offset({date(2026, 6, 8): (95.0, 78.0)}, {})
    assert off == {"high": 0.0, "low": 0.0, "n_days": 0}


from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _member(day, peak):
    """A synthetic 24-hour member peaking at `peak` at 15:00 local."""
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]  # max=peak, min=peak-15
    return times, temps


def _series(day):
    return {"det_a": _member(day, 90.0), "det_b": _member(day, 92.0)}


def test_settle_offset_shifts_consensus_and_distribution():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    base = model.predict_variable(series, obs, day, "high", None, None)
    plus = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 1.0, "low": 0.0})
    assert base["consensus"] == 91.0
    assert plus["consensus"] == 92.0
    # Constant shift must not change the spread, only the location.
    assert plus["sigma_used"] == base["sigma_used"]
    assert (model.prob_at_least(plus["probabilities"], 92)
            > model.prob_at_least(base["probabilities"], 92))


def test_zero_offset_is_identical_to_none_robinhood_guard():
    day = date(2030, 7, 1)
    series, obs = _series(day), {"obs": ([], [])}
    base = model.predict_variable(series, obs, day, "high", None, None)
    zero = model.predict_variable(series, obs, day, "high", None, None,
                                  {"high": 0.0, "low": 0.0})
    assert base == zero


def test_predict_from_threads_offset():
    day = date(2030, 7, 1)
    pf = model._predict_from(_series(day), {"obs": ([], [])}, day, None, None,
                             {"high": 1.0, "low": 0.0})
    assert pf["high"]["consensus"] == 92.0
