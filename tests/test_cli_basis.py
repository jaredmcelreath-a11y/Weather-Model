"""Tests for the Kalshi CLI settlement basis (Part A): CLI truth fetch parsing,
the calibrated settlement offset, and the model's settle_offset shift."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE
from sources.station_history import _parse_daily

_TZ = ZoneInfo(TIMEZONE)

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
    # low offset is 0.0 -> low consensus stays at the unshifted value (peak-15).
    base_low = model.predict_variable(_series(day), {"obs": ([], [])}, day,
                                      "low", None, None)
    assert pf["low"]["consensus"] == base_low["consensus"]


import json

import forecast_log


def _snap():
    return {
        "updated": "2026-06-20T15:00:00",
        "today": {
            "day": "2026-06-20",
            "high": {"consensus": 91, "probabilities": {"90": 0.5, "91": 0.5}},
            "low": {"consensus": 75, "probabilities": {"74": 0.5, "75": 0.5}},
        },
        "tomorrow": {
            "day": "2026-06-21",
            "high": {"consensus": 93, "probabilities": {"92": 0.5, "93": 0.5}},
            "low": {"consensus": 77, "probabilities": {"76": 0.5, "77": 0.5}},
        },
    }


def test_hourly_and_cli_records_coexist(tmp_path):
    p = str(tmp_path / "log.jsonl")
    forecast_log.record(_snap(), path=p, basis="hourly")
    forecast_log.record(_snap(), path=p, basis="cli")
    rows = forecast_log.load(p)
    assert {r["basis"] for r in rows} == {"hourly", "cli"}
    assert len([r for r in rows if r["basis"] == "hourly"]) == 4
    assert len([r for r in rows if r["basis"] == "cli"]) == 4


def test_legacy_untagged_record_treated_as_hourly(tmp_path):
    p = str(tmp_path / "log.jsonl")
    legacy = {"target_date": "2026-06-20", "variable": "high", "lead_bucket": 0,
              "captured_at": "x", "consensus": 91, "probabilities": {"91": 1.0}}
    with open(p, "w") as fh:
        fh.write(json.dumps(legacy) + "\n")
    forecast_log.record(_snap(), path=p, basis="hourly")
    rows = forecast_log.load(p)
    match = [r for r in rows
             if r["target_date"] == "2026-06-20" and r["variable"] == "high"
             and r["lead_bucket"] == 0 and r.get("basis", "hourly") == "hourly"]
    assert len(match) == 1


import scoring
from sources import station_history


def test_score_filters_by_basis_and_uses_matching_truth(monkeypatch):
    recs = [
        {"target_date": "2026-06-10", "variable": "high", "lead_bucket": 0,
         "basis": "hourly", "consensus": 90, "probabilities": {"90": 1.0}},
        {"target_date": "2026-06-10", "variable": "high", "lead_bucket": 0,
         "basis": "cli", "consensus": 91, "probabilities": {"91": 1.0}},
    ]
    monkeypatch.setattr(scoring.forecast_log, "load", lambda path=None: recs)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {date(2026, 6, 10): (90.0, 70.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {date(2026, 6, 10): (91.0, 70.0)})
    today = date(2026, 6, 11)
    h = scoring.score(today=today, basis="hourly")
    c = scoring.score(today=today, basis="cli")
    assert h["n_settled"] == 1 and c["n_settled"] == 1
    assert h["by_variable"]["high"]["brier"] == 0.0
    assert c["by_variable"]["high"]["brier"] == 0.0


import backtest
import calibration
from sources import open_meteo_models


def test_backtest_cli_uses_cli_truth_and_applies_offset(monkeypatch):
    day = date(2026, 6, 10)
    series = {"det_a": _member(day, 90.0)}  # daily high 90, low 75
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e: series)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {day: (90.0, 75.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {day: (91.0, 75.0)})
    monkeypatch.setattr(calibration, "get", lambda refresh=True: {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0}})

    hourly = backtest.run()
    cli_off = backtest.run(cli=True, settle_offset={"high": 1.0, "low": 0.0})
    cli_no = backtest.run(cli=True)

    assert hourly["high"]["mae"] == 0.0     # consensus 90 vs hourly 90
    assert cli_off["high"]["mae"] == 0.0    # consensus 90+1=91 vs cli 91
    assert cli_no["high"]["mae"] == 1.0     # consensus 90 vs cli 91 -> off by 1


def test_scheduled_log_records_both_bases(monkeypatch):
    import scheduled_log
    import model

    monkeypatch.setattr(calibration, "get",
                        lambda refresh=True: {"settlement_offset": {"high": 1.0, "low": 0.0}})
    monkeypatch.setattr(model, "snapshot",
                        lambda calib, settle_offset=None: {"_off": settle_offset})
    calls = []
    monkeypatch.setattr(scheduled_log.forecast_log, "record",
                        lambda snap, basis="hourly": calls.append((snap.get("_off"), basis)))
    monkeypatch.setattr(scheduled_log.forecast_log, "load", lambda path=None: [])

    scheduled_log.main()

    assert (None, "hourly") in calls                       # hourly snapshot, no offset
    assert ({"high": 1.0, "low": 0.0}, "cli") in calls     # offset snapshot, cli basis
