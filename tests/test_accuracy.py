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

def test_forecast_log_persists_per_source_means(tmp_path):
    p = str(tmp_path / "log.jsonl")
    now = datetime(2026, 6, 16, 22, tzinfo=TZ)
    snap = _snapshot(now)
    # Attach a per-source extremes block like model.snapshot() produces.
    snap["sources"] = {
        "today": {
            "ensemble": {"ens_a": (96.0, 77.0), "ens_b": (94.0, 75.0)},
            "deterministic": {"det_x": (95.0, 76.0)},
            "nws": {"nws_ndfd": (97.0, 78.0)},
        },
        "tomorrow": {},
    }
    forecast_log.record(snap, path=p)
    rows = {(r["target_date"], r["variable"]): r for r in forecast_log.load(p)}
    hi = rows[(TODAY.isoformat(), "high")]["sources"]
    assert hi["ensemble"] == 95.0          # mean(96, 94)
    assert hi["deterministic"] == 95.0
    assert hi["nws"] == 97.0
    lo = rows[(TODAY.isoformat(), "low")]["sources"]
    assert lo["ensemble"] == 76.0          # mean(77, 75)
    # tomorrow had no source block -> record omits the key (back-compatible)
    assert "sources" not in rows[((TODAY + timedelta(days=1)).isoformat(), "high")]


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


def test_score_exact_bin_metrics(tmp_path, monkeypatch):
    p = str(tmp_path / "log.jsonl")
    captured = datetime(2026, 6, 16, 22, tzinfo=TZ)
    forecast_log.record(_snapshot(captured), path=p)
    monkeypatch.setattr(forecast_log, "_PATH", p)
    # Today high peak bin is a tie {95,96}; max() picks "95". Settle high=96 so the
    # peak misses but is within ±1; low peak "77" settles exactly. Tomorrow high
    # peak "96" settles 96 (hit); low peak "78" settles 78 (hit).
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {TODAY: (96, 77),
                                      TODAY + timedelta(days=1): (96, 78)})
    res = scoring.score(today=date(2026, 6, 18))
    hi, lo = res["by_variable"]["high"], res["by_variable"]["low"]
    # high: 1/2 exact peak (tomorrow hits, today's 95 misses 96), both within ±1
    assert hi["exact_peak"] == 50 and hi["within1"] == 100
    # low: both exact
    assert lo["exact_peak"] == 100 and lo["within1"] == 100
    # consensus: today high consensus 95 misses 96; tomorrow 96 hits -> 50%
    assert hi["exact_consensus"] == 50
    # broken out by lead, same-day (0) and day-ahead (24) both present
    assert res["by_lead"][0]["high"]["exact_peak"] == 0      # today high 95 vs 96
    assert res["by_lead"][24]["high"]["exact_peak"] == 100   # tomorrow high 96 vs 96


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


def test_run_intraday_anchors_to_observations(monkeypatch):
    # A clean diurnal day peaking at 95 / troughing at 75; obs equal the forecast.
    # By a late hour the whole day is observed, so the peak bin must be the exact
    # settled degree (95) — the harness proves anchoring sharpens same-day.
    days = [date(2026, 6, 14), date(2026, 6, 15)]
    base0 = datetime(2026, 6, 14, tzinfo=TZ)
    end_dt = datetime(2026, 6, 16, tzinfo=TZ)
    times, temps = [], []
    t = base0
    while t < end_dt:
        # peak 95 at 15:00, trough 75 at 03:00
        temps.append(85 - 10 * math.cos((t.hour - 3) / 24 * 2 * math.pi))
        times.append(t)
        t += timedelta(hours=1)
    det = {"det_gfs_seamless": (times, temps)}

    monkeypatch.setattr(backtest, "_TZ", TZ)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {d: (95.0, 75.0) for d in days})
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e: det)
    monkeypatch.setattr(station_history, "_fetch_series", lambda s, e: (times, temps))
    monkeypatch.setattr(backtest, "to_hourly", lambda ti, te: (ti, te))
    monkeypatch.setattr(backtest.calibration, "get", lambda refresh=True: {})

    m = backtest.run_intraday(days=2, hours=(10, 19))
    # late in the day the high is fully observed -> exact bin every day
    assert m[19]["high"] == 100.0
    assert m[19]["n"] == 2


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
