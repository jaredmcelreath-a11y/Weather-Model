"""Tests for the accuracy upgrades: forecast log, lead bucketing, live scoring,
reliability binning, and the radiational-cooling offset. All synthetic — no
network — so the logic is exercised independently of live data.
"""

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import backtest
import forecast_log
import model
import scoring
from config import TIMEZONE, lead_bucket
from sources import open_meteo_models, station_history

TZ = ZoneInfo(TIMEZONE)
TODAY = date(2026, 6, 16)


def _snapshot(now):
    return {
        "updated": now.isoformat(),
        "today": {"day": TODAY.isoformat(),
                  "high": {"consensus": 95, "probabilities": {"95": 0.5, "96": 0.5}},
                  "low": {"consensus": 77, "probabilities": {"77": 1.0}}},
        "tomorrow": {"day": (TODAY + timedelta(days=1)).isoformat(),
                     "high": {"consensus": 96, "probabilities": {"96": 1.0}},
                     "low": {"consensus": 78, "probabilities": {"78": 1.0}}},
    }


# --- lead bucketing ---

def test_lead_bucket_boundaries():
    now = datetime(2026, 6, 16, 22, tzinfo=TZ)
    assert lead_bucket(now, TODAY) == 0                       # same day
    assert lead_bucket(now, TODAY + timedelta(days=1)) == 24  # tomorrow
    assert lead_bucket(now, TODAY + timedelta(days=2)) == 36  # further out
    assert lead_bucket(now, TODAY - timedelta(days=1)) == 0   # past clamps to 0


# --- forecast log ---

def test_forecast_log_upsert(tmp_path):
    p = str(tmp_path / "log.jsonl")
    now = datetime(2026, 6, 16, 22, tzinfo=TZ)
    forecast_log.record(_snapshot(now), path=p)
    forecast_log.record(_snapshot(now), path=p)  # rerun -> upsert, not append
    rows = forecast_log.load(p)
    assert len(rows) == 4  # {today, tomorrow} x {high, low}
    by_key = {(r["target_date"], r["variable"]): r for r in rows}
    assert by_key[(TODAY.isoformat(), "high")]["lead_bucket"] == 0
    tom = (TODAY + timedelta(days=1)).isoformat()
    assert by_key[(tom, "low")]["lead_bucket"] == 24


# --- live scoring ---

def test_score_against_actuals(tmp_path, monkeypatch):
    p = str(tmp_path / "log.jsonl")
    # Capture on 6/16 -> today/tomorrow buckets 0/24; both settle before 6/18.
    captured = datetime(2026, 6, 16, 22, tzinfo=TZ)
    forecast_log.record(_snapshot(captured), path=p)
    monkeypatch.setattr(forecast_log, "_PATH", p)
    # Actuals: high 96 (so "95"/"96" each half right), low 77.
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {TODAY: (96, 77),
                                      TODAY + timedelta(days=1): (96, 78)})
    result = scoring.score(today=date(2026, 6, 18))
    assert result["n_settled"] == 4
    assert "high" in result["by_variable"] and "low" in result["by_variable"]
    assert result["by_variable"]["low"]["brier"] >= 0
    # per-lead residuals captured for both buckets
    assert set(result["by_lead"]) == {0, 24}


def test_score_empty_log_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "_PATH", str(tmp_path / "none.jsonl"))
    assert scoring.score(today=TODAY) == {"n_settled": 0, "by_variable": {}, "by_lead": {}}


# --- reliability binning ---

def test_contract_points_and_reliability():
    probs = {"88": 0.1, "89": 0.2, "90": 0.4, "91": 0.2, "92": 0.1}
    pts = backtest.contract_points(probs, 90, "high")
    # Greater-than strikes 88 (p .9, won) and 89 (p .7, won) resolve YES at high=90;
    # 90 (p .3) and 91 (p .1) resolve NO.
    assert (0.9, 1.0) in [(round(p, 1), o) for p, o in pts]
    assert (0.3, 0.0) in [(round(p, 1), o) for p, o in pts]
    bins = backtest.reliability_bins([(0.05, 0.0), (0.95, 1.0)], n=10)
    assert {b["n"] for b in bins} == {1}


# --- radiational cooling ---

def _diurnal_series():
    base = datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=TZ)
    times = [base + timedelta(hours=h) for h in range(48)]
    out = {}
    for i, off in enumerate([-2, -1, 0, 1, 2]):
        temps = [75 + 15 * math.sin((h % 24 - 9) / 24 * 2 * math.pi) + off
                 for h in range(48)]
        out[f"ens_m{i}"] = (times, temps)
    return out


def test_cooling_offset_applied_on_clear_calm(monkeypatch):
    s, obs = _diurnal_series(), {"obs": ([], [])}
    now = datetime(TODAY.year, TODAY.month, TODAY.day, 22, tzinfo=TZ)
    tom = TODAY + timedelta(days=1)
    calib = {"cooling": {"low_offset": 3.0, "cloud_thresh": 30, "wind_thresh": 10}}

    monkeypatch.setattr(open_meteo_models, "night_conditions",
                        lambda day, forecast_days=2: (10.0, 5.0))  # clear, calm
    cool = model.predict_variable(s, obs, tom, "low", now, calib)
    monkeypatch.setattr(open_meteo_models, "night_conditions",
                        lambda day, forecast_days=2: (80.0, 20.0))  # cloudy, windy
    warm = model.predict_variable(s, obs, tom, "low", now, calib)

    assert cool["cooling_applied"] and not warm["cooling_applied"]
    assert round(warm["consensus"] - cool["consensus"], 1) == 3.0
