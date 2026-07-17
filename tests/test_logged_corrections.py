"""The self-correction knobs (by_lead lead-bias, warm_low night-bias) are baked
into the logged consensus/probabilities; to disentangle the feedback loop the
model surfaces which corrections it applied, and both logs record them.

Sign convention: each value is the amount SUBTRACTED from the samples, so the
raw (pre-correction) consensus = logged consensus + sum(corrections.values()).
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import betting_log
import forecast_log
import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]   # max=peak, min=peak-15
    return times, temps


def _series(day, peaks=(92.0, 94.0)):
    return {f"det_{i}": _member(day, p) for i, p in enumerate(peaks)}


_BASE = {"bias": {"deterministic": {"high": 0.0, "low": 0.0}},
         "sigma": {"high": 2.0, "low": 2.0}}


# --- model surfaces the applied corrections ---

def test_predict_variable_records_by_lead_correction():
    day = date(2030, 1, 1)
    calib = dict(_BASE, bias_correction={"by_lead": {"0": {"high": 0.6}}})
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "high",
                                 None, calib)
    assert out["corrections"] == {"by_lead": 0.6}
    assert out["consensus"] == 92.4                     # mean(92,94)=93 - 0.6
    # raw consensus is recoverable
    assert round(out["consensus"] + out["corrections"]["by_lead"], 1) == 93.0


def test_predict_variable_records_warm_low_correction():
    day = date(2030, 1, 1)
    calib = dict(_BASE, bias_correction={"warm_low": {"threshold": 76, "bias": -0.5}})
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, calib)
    assert out["corrections"] == {"warm_low": -0.5}
    assert out["consensus"] == 78.5                     # lows 77,79 -> 78 - (-0.5)


def test_predict_variable_corrections_empty_when_none_apply():
    day = date(2030, 1, 1)
    calib = dict(_BASE)                                  # no bias_correction knobs
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "high",
                                 None, calib)
    assert out["corrections"] == {}


def test_predict_variable_corrections_empty_when_obs_anchored():
    # Corrections skip the obs-anchored path, so a same-day forecast with morning
    # obs records no correction even when the knob exists.
    day = date.today()
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    now = base + timedelta(hours=8)
    obs = {"obs": ([base + timedelta(hours=h) for h in range(9)],
                   [82.0 - h * 0.4 for h in range(9)])}
    calib = dict(_BASE, bias_correction={"warm_low": {"threshold": 76, "bias": -0.5}})
    out = model.predict_variable(_series(day), obs, day, "low", now, calib)
    assert out["corrections"] == {}


# --- forecast_log records them (only when non-empty) ---

def _snapshot(day, corrections):
    var = {"day": day, "high": {"consensus": 92.4, "probabilities": {"92": 1.0}}}
    if corrections is not None:
        var["high"]["corrections"] = corrections
    return {"updated": f"{day}T09:00:00-06:00", "today": var}


def test_forecast_log_records_corrections(tmp_path):
    p = str(tmp_path / "f.jsonl")
    forecast_log.record(_snapshot("2030-01-01", {"by_lead": 0.6}), path=p, basis="cli")
    row = next(r for r in forecast_log.load(p) if r["variable"] == "high")
    assert row["corrections"] == {"by_lead": 0.6}


def test_forecast_log_omits_empty_corrections(tmp_path):
    p = str(tmp_path / "f.jsonl")
    forecast_log.record(_snapshot("2030-01-02", {}), path=p, basis="cli")
    row = next(r for r in forecast_log.load(p) if r["variable"] == "high")
    assert "corrections" not in row
    # a pred with no corrections key at all is also fine
    forecast_log.record(_snapshot("2030-01-03", None), path=p, basis="cli")
    row = next(r for r in forecast_log.load(p) if r["target_date"] == "2030-01-03")
    assert "corrections" not in row


# --- betting_log records them ---

_CALIB = {"settlement_offset": {"high": 0.0, "high_std": 0.0, "low": 0.0, "low_std": 0.0}}


def test_betting_log_records_corrections(tmp_path):
    p = str(tmp_path / "b.jsonl")
    cli = {"today": {"day": "2030-01-01",
                     "low": {"consensus": 78.5, "probabilities": {"78": 1.0},
                             "observed_so_far": None, "observed_continuous": None,
                             "peak_locked": False, "sigma_used": 2.0,
                             "corrections": {"warm_low": -0.5}}}}
    betting_log.record(cli, {}, "sr", _CALIB, path=p)
    row = next(r for r in betting_log.load(p) if r["variable"] == "low")
    assert row["corrections"] == {"warm_low": -0.5}
