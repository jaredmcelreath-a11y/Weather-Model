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


def test_forecast_log_persists_market_block(tmp_path):
    p = str(tmp_path / "log.jsonl")
    now = datetime(2026, 6, 16, 22, tzinfo=TZ)
    snap = _snapshot(now)
    snap["market"] = {"today": {"high": {"ev": 95.5, "buckets": [[94, 96, 1.0]],
                                         "volume": 30.0}}}
    forecast_log.record(snap, path=p, basis="cli")
    rows = {(r["target_date"], r["variable"]): r for r in forecast_log.load(p)}
    assert rows[(TODAY.isoformat(), "high")]["market"]["ev"] == 95.5
    # variables/days without a market block omit the key (back-compatible)
    assert "market" not in rows[(TODAY.isoformat(), "low")]


def test_forecast_log_stamps_regime_flags(tmp_path):
    p = str(tmp_path / "log.jsonl")
    now = datetime(2026, 6, 16, 22, tzinfo=TZ)
    snap = _snapshot(now)
    snap["today"]["low"]["convective_widened"] = True
    snap["today"]["low"]["front_widened"] = False
    snap["tomorrow"]["high"]["front_widened"] = True
    forecast_log.record(snap, path=p)
    rows = {(r["target_date"], r["variable"]): r for r in forecast_log.load(p)}
    assert rows[(TODAY.isoformat(), "low")]["convective_widened"] is True
    # falsy or absent flags are omitted entirely (calm rows stay byte-identical)
    assert "front_widened" not in rows[(TODAY.isoformat(), "low")]
    assert "convective_widened" not in rows[(TODAY.isoformat(), "high")]
    tom = (TODAY + timedelta(days=1)).isoformat()
    assert rows[(tom, "high")]["front_widened"] is True


def test_market_accuracy_compares_to_model(tmp_path, monkeypatch):
    p = str(tmp_path / "log.jsonl")
    captured = datetime(2026, 6, 16, 22, tzinfo=TZ)
    snap = _snapshot(captured)
    # Model high consensus is 95 (from _snapshot); give the market a sharper 96.
    snap["market"] = {"today": {"high": {"ev": 96.0, "buckets": [], "volume": 5}}}
    forecast_log.record(snap, path=p, basis="cli")
    monkeypatch.setattr(forecast_log, "_PATH", p)
    # CLI settlement high = 96 -> market (96) nails it, model (95) is 1 off.
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {TODAY: (96, 77),
                                      TODAY + timedelta(days=1): (96, 78)})
    res = scoring.market_accuracy(today=date(2026, 6, 18))
    hi = res["by_variable"]["high"]
    assert hi["market_mae"] == 0.0 and hi["model_mae"] == 1.0
    assert hi["market_closer_pct"] == 100


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


def _intraday_obs(day, peak_hour, peak, now_hour, drop_after=0.0):
    """Hourly obs rising to `peak` at `peak_hour`, then falling `drop_after` per
    hour, up to `now_hour`."""
    base = datetime(day.year, day.month, day.day, tzinfo=TZ)
    times, temps = [], []
    for h in range(0, now_hour + 1):
        if h <= peak_hour:
            temps.append(peak - (peak_hour - h))           # rise 1°/hr to peak
        else:
            temps.append(peak - drop_after * (h - peak_hour))
        times.append(base + timedelta(hours=h))
    return times, temps


def test_extreme_locked_detects_descent():
    day = date(2026, 6, 16)
    # high peaked at 14:00=95, now 16:00 fallen to 92 (-3): locked.
    t, v = _intraday_obs(day, peak_hour=14, peak=95, now_hour=16, drop_after=1.5)
    now = datetime(2026, 6, 16, 16, tzinfo=TZ)
    assert model._extreme_locked(t, v, day, "high", now, drop=2.0) is True
    # still sitting at the peak (no descent) -> not locked.
    t2, v2 = _intraday_obs(day, peak_hour=14, peak=95, now_hour=16, drop_after=0.0)
    assert model._extreme_locked(t2, v2, day, "high", now, drop=2.0) is False


def test_warm_overnight_does_not_false_lock_high():
    day = date(2026, 6, 24)
    # Warm summer night: temp still 84 just after midnight, cooling to the
    # pre-dawn minimum of 79 by 07:00. The real diurnal peak (96) is hours away.
    base = datetime(day.year, day.month, day.day, tzinfo=TZ)
    hours = list(range(0, 8))
    temps = [84, 83, 82, 81, 80, 79.5, 79, 79]   # running max 84 @00:00, min 79 @06:00
    t = [base + timedelta(hours=h) for h in hours]
    now = datetime(2026, 6, 24, 7, 30, tzinfo=TZ)
    # Retreat from the overnight max is 5°F (>= 2.0 drop), but that max is an
    # overnight value preceding the morning minimum -> the high has NOT peaked.
    assert model._extreme_locked(t, temps, day, "high", now, drop=2.0) is False


def test_warm_overnight_high_follows_forecast(monkeypatch):
    day = date(2026, 6, 24)
    now = datetime(2026, 6, 24, 7, 30, tzinfo=TZ)
    base = datetime(day.year, day.month, day.day, tzinfo=TZ)
    ftimes = [base + timedelta(hours=h) for h in range(24)]
    # Realistic diurnal forecast: cool morning (~79 at 07:00) rising to a 96 peak
    # at 16:00, then easing off through the evening.
    def fday(h):
        if h <= 6:
            return 81 - (6 - h) * 0.3       # mild overnight cooling toward dawn
        if h <= 16:
            return 79 + (h - 7) * (96 - 79) / 9.0   # 79 @07:00 -> 96 @16:00
        return 96 - (h - 16) * 1.5
    fc = {"det_a": (ftimes, [fday(h) for h in range(24)])}
    # Obs: warm overnight 84, cooled to 79 by 07:00 (no real peak yet).
    ot = [base + timedelta(hours=h) for h in range(8)]
    ov = [84, 83, 82, 81, 80, 79.5, 79, 79]
    out = model.predict_variable(fc, {"obs": (ot, ov)}, day, "high", now, None)
    assert out["peak_locked"] is False
    assert out["consensus"] >= 94            # follows the forecast peak, not the overnight 84


def test_lock_collapses_high_to_observed(monkeypatch):
    day = date(2026, 6, 16)
    now = datetime(2026, 6, 16, 16, tzinfo=TZ)
    # Forecast members that (wrongly) project a further rise to 97.
    base = datetime(day.year, day.month, day.day, tzinfo=TZ)
    ftimes = [base + timedelta(hours=h) for h in range(24)]
    fc = {"det_a": (ftimes, [97 - abs(h - 18) for h in range(24)])}  # forecast peak 97 @18h
    # Obs: real peak 95 at 14:00, now fallen to 92 -> peak locked.
    ot, ov = _intraday_obs(day, peak_hour=14, peak=95, now_hour=16, drop_after=1.5)
    out = model.predict_variable(fc, {"obs": (ot, ov)}, day, "high", now, None)
    assert out["peak_locked"] is True
    assert out["consensus"] == 95.0          # locked to realized max, not the 97 forecast


def test_continuous_bound_captures_spike():
    day = date(2026, 6, 16)
    now = datetime(2026, 6, 16, 16, tzinfo=TZ)
    base = datetime(day.year, day.month, day.day, tzinfo=TZ)
    ftimes = [base + timedelta(hours=h) for h in range(24)]
    fc = {"det_a": (ftimes, [88 - abs(h - 15) for h in range(24)])}   # forecast high ~88
    # Hourly obs top out at 88; a sustained sub-hourly peak hit 91 across several
    # 5-minute readings (a lone single-sample spike is now rejected as sensor
    # noise — see test_robust_extreme_rejects_lone_spike).
    ot = [base + timedelta(hours=h) for h in range(17)]
    ov = [88 - abs(h - 14) for h in range(17)]
    spike_t = ot + [base + timedelta(hours=14, minutes=m) for m in (15, 20, 25)]
    spike_v = ov + [91.0, 91.0, 91.0]
    obs = {"obs": (ot, ov), "obs_continuous": (spike_t, spike_v)}
    out = model.predict_variable(fc, obs, day, "high", now, None)
    # The corroborated peak (91 - 0.9 = 90.1) floors the distribution: no mass below 90.
    assert model.prob_at_most(out["probabilities"], 89) < 1e-9
    # Without the continuous feed, the 88 hourly bound leaves mass at 88-89.
    out0 = model.predict_variable(fc, {"obs": (ot, ov)}, day, "high", now, None)
    assert model.prob_at_most(out0["probabilities"], 89) > 0.1


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


# --- self-correction: lead-time bias ---

def test_per_lead_bias_shrinks_and_gates(monkeypatch):
    fake = {(24, "high"): [1.5] * 10,        # unanimous 1.5 -> significant
            (24, "low"): [0.1, -0.1] * 5,    # median 0 -> gate fails
            (0, "high"): [2.0] * 5}          # below MIN_LEAD_DAYS -> dropped
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": fake)
    out = scoring.per_lead_bias()
    # high@24: median 1.5, sd 0 -> SE 0, passes; shrink 1.5 * 10/(10+8) = 0.83
    assert out[24]["high"] == 0.83
    assert "low" not in out.get(24, {})
    assert 0 not in out


def test_per_lead_bias_empty_when_no_data(monkeypatch):
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": {})
    assert scoring.per_lead_bias() == {}




def test_per_lead_estimators_forward_basis_and_today(monkeypatch):
    seen = []
    def fake(today=None, basis="hourly"):
        seen.append((today, basis))
        return {}
    monkeypatch.setattr(scoring, "_correction_residuals", fake)
    scoring.per_lead_bias(today=date(2026, 6, 20), basis="cli")
    scoring.per_lead_sigma(basis="cli")
    assert seen[0] == (date(2026, 6, 20), "cli")
    assert seen[1][1] == "cli"


def test_median_immune_to_storm_outliers(monkeypatch):
    # 18 calm nights (median ~0) + the three June-style storm misses. The
    # median-based estimator must emit nothing; the sanity block shows the old
    # mean-based estimator WOULD have cleared its own gate on the same pool.
    errs = [0.1, -0.1, 0.2, -0.2, 0.0, 0.1, -0.1, 0.0, 0.2, -0.2,
            0.0, 0.1, -0.1, 0.0, 0.2, -0.2, 0.0, 0.1] + [3.7, 2.7, 3.6]
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": {(0, "low"): errs})
    assert scoring.per_lead_bias() == {}
    mean = sum(errs) / len(errs)
    sd = (sum((e - mean) ** 2 for e in errs) / len(errs)) ** 0.5
    assert abs(mean) > sd / len(errs) ** 0.5   # the outlier-driven mean was "significant"


def test_consistent_bias_survives_median(monkeypatch):
    # A genuine persistent warm bias (like the day-ahead high) must still emit.
    errs = [1.0, 1.1, 0.9, 1.0, 1.2, 0.8, 1.0, 1.1, 0.9, 1.0,
            1.1, 0.9, 1.0, 1.2, 0.8, 1.0, 1.1, 0.9, 1.0, 1.0]
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": {(24, "high"): errs})
    out = scoring.per_lead_bias()
    assert out[24]["high"] == round(1.0 * 20 / 28, 2)   # median 1.0, shrunk


def test_per_lead_sigma_std_over_pool(monkeypatch):
    errs = [1.0, -1.0] * 5                    # mean 0, population sd exactly 1.0
    monkeypatch.setattr(scoring, "_correction_residuals",
                        lambda today=None, basis="hourly": {(24, "low"): errs})
    assert scoring.per_lead_sigma() == {24: {"low": 1.0}}


def test_correction_pool_windows_and_excludes_flags(tmp_path, monkeypatch):
    p = str(tmp_path / "log.jsonl")
    today = date(2026, 6, 18)
    old_day = today - timedelta(days=60)
    rows = [
        # in-window but storm-flagged -> excluded from the pool
        {"target_date": TODAY.isoformat(), "variable": "low", "lead_bucket": 0,
         "consensus": 81.7, "probabilities": {"81": 1.0}, "convective_widened": True},
        # in-window, clean -> kept
        {"target_date": TODAY.isoformat(), "variable": "high", "lead_bucket": 0,
         "consensus": 95.0, "probabilities": {"95": 1.0}},
        # clean but 60 days old -> outside the 45-day window
        {"target_date": old_day.isoformat(), "variable": "high", "lead_bucket": 0,
         "consensus": 90.0, "probabilities": {"90": 1.0}},
    ]
    forecast_log._write(rows, p)
    monkeypatch.setattr(forecast_log, "_PATH", p)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {TODAY: (96, 78), old_day: (91, 70)})
    pool = scoring._correction_residuals(today=today)
    assert (0, "low") not in pool              # flagged record excluded
    assert pool[(0, "high")] == [-1.0]         # only the windowed clean record


def test_correction_exclusions_counts_windowed_flags(tmp_path, monkeypatch):
    p = str(tmp_path / "log.jsonl")
    today = date(2026, 6, 18)
    old_day = today - timedelta(days=60)
    rows = [
        # flagged, in window, right basis -> counted
        {"target_date": TODAY.isoformat(), "variable": "low", "lead_bucket": 0,
         "basis": "cli", "consensus": 81.7, "probabilities": {"81": 1.0},
         "front_widened": True},
        # flagged but stale -> not counted
        {"target_date": old_day.isoformat(), "variable": "low", "lead_bucket": 0,
         "basis": "cli", "consensus": 70.0, "probabilities": {"70": 1.0},
         "convective_widened": True},
        # clean, in window -> not counted
        {"target_date": TODAY.isoformat(), "variable": "high", "lead_bucket": 0,
         "basis": "cli", "consensus": 95.0, "probabilities": {"95": 1.0}},
        # flagged, in window, WRONG basis -> not counted
        {"target_date": TODAY.isoformat(), "variable": "low", "lead_bucket": 24,
         "basis": "hourly", "consensus": 80.0, "probabilities": {"80": 1.0},
         "convective_widened": True},
    ]
    forecast_log._write(rows, p)
    monkeypatch.setattr(forecast_log, "_PATH", p)
    assert scoring.correction_exclusions(today=today, basis="cli") == 1


def test_bias_correction_block_wraps_scoring(monkeypatch):
    import calibration
    seen = {}
    def fake_bias(basis="hourly"):
        seen["basis"] = basis
        return {24: {"high": -1.1}}
    monkeypatch.setattr(scoring, "per_lead_bias", fake_bias)
    assert calibration._bias_correction() == {"by_lead": {24: {"high": -1.1}}}
    # the Kalshi/CLI site means calibration must source CLI-basis self-scoring
    assert seen["basis"] == "cli"
    # scoring failure must degrade to an empty (no-op) block, never raise
    def boom(basis="hourly"):
        raise RuntimeError("scoring down")
    monkeypatch.setattr(scoring, "per_lead_bias", boom)
    assert calibration._bias_correction() == {"by_lead": {}}


def test_lead_bias_correction_shifts_consensus():
    s, obs = _diurnal_series(), {"obs": ([], [])}
    now = datetime(TODAY.year, TODAY.month, TODAY.day, 22, tzinfo=TZ)
    tom = TODAY + timedelta(days=1)          # bucket 24, pure forecast (no obs)
    base = model.predict_variable(s, obs, tom, "high", now, {})
    calib = {"bias_correction": {"by_lead": {"24": {"high": 1.5}}}}
    corr = model.predict_variable(s, obs, tom, "high", now, calib)
    # forecast measured 1.5 warm at day-ahead -> consensus drops by 1.5
    assert round(base["consensus"] - corr["consensus"], 1) == 1.5


def test_lead_bias_skipped_when_observed():
    day = TODAY
    now = datetime(TODAY.year, TODAY.month, TODAY.day, 16, tzinfo=TZ)
    ot, ov = _intraday_obs(day, peak_hour=14, peak=95, now_hour=16, drop_after=0.5)
    s = _diurnal_series()
    calib = {"bias_correction": {"by_lead": {"0": {"high": 2.0}}}}
    out = model.predict_variable(s, {"obs": (ot, ov)}, day, "high", now, calib)
    out0 = model.predict_variable(s, {"obs": (ot, ov)}, day, "high", now, {})
    # obs are anchoring the day -> forecast de-bias must NOT apply
    assert out["consensus"] == out0["consensus"]


def test_active_corrections_lists_live_knobs():
    import calibration
    calib = {"bias_correction": {"by_lead": {"24": {"high": -1.2}}},
             "sigma": {"by_lead": {"24": {"low": 1.8}}}}
    out = calibration.active_corrections(calib)
    assert "day-ahead high -1.2°F bias" in out
    assert "day-ahead low σ=1.8" in out
    # nothing live -> empty list (dormant)
    assert calibration.active_corrections(None) == []
    assert calibration.active_corrections({}) == []
