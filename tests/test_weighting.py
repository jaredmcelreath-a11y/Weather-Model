"""Skill-weighted, group-rebalanced consensus."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import backtest
import calibration
import model
from config import TIMEZONE
from sources import open_meteo_ensemble, open_meteo_models, station_history
from sources import common

_TZ = ZoneInfo(TIMEZONE)


def test_fetch_historical_parses_members(monkeypatch):
    fake = {"hourly": {
        "time": ["2026-05-01T00:00", "2026-05-01T01:00"],
        "temperature_2m_member01_ncep_gefs_seamless": [70.0, 71.0],
        "temperature_2m_member02_ncep_gefs_seamless": [69.0, 72.0],
        "temperature_2m": [70.5, 71.5],   # control column
    }}
    monkeypatch.setattr(common, "get_json", lambda *a, **k: fake)
    monkeypatch.setattr(open_meteo_ensemble, "get_json", lambda *a, **k: fake)
    out = open_meteo_ensemble.fetch_historical(date(2026, 5, 1), date(2026, 5, 1))
    assert "ens_member01_ncep_gefs_seamless" in out
    assert "ens_control" in out
    times, temps = out["ens_member01_ncep_gefs_seamless"]
    assert len(times) == 2 and temps == [70.0, 71.0]


def test_bin_probabilities_uniform_weights_match_unweighted():
    samples = [88.0, 90.0, 92.0]
    a = model._bin_probabilities(samples, 2.0)
    b = model._bin_probabilities(samples, 2.0, weights=[1.0, 1.0, 1.0])
    assert a == b


def test_bin_probabilities_weight_shifts_mass():
    samples = [85.0, 95.0]
    low_heavy = model._bin_probabilities(samples, 2.0, weights=[9.0, 1.0])
    high_heavy = model._bin_probabilities(samples, 2.0, weights=[1.0, 9.0])
    assert model.prob_at_least(low_heavy, 95) < model.prob_at_least(high_heavy, 95)


def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    return times, [peak - abs(h - 15) for h in range(24)]


def test_sample_weights_split_ensemble_mass_across_members():
    series = {"ens_a": None, "ens_b": None, "det_gfs_seamless": None, "nws_x": None}
    w = model._sample_weights(series, {"ensemble_mean": 0.6,
                                       "det_gfs_seamless": 0.3, "nws": 0.1})
    assert abs(w["ens_a"] - 0.3) < 1e-9      # 0.6 / 2 members
    assert abs(w["ens_b"] - 0.3) < 1e-9
    assert abs(w["det_gfs_seamless"] - 0.3) < 1e-9
    assert abs(w["nws_x"] - 0.1) < 1e-9


def test_consensus_unchanged_without_weights():
    day = date(2030, 7, 1)
    series = {"det_a": _member(day, 90.0), "det_b": _member(day, 92.0)}
    out = model.predict_variable(series, {"obs": ([], [])}, day, "high", None, None)
    assert out["consensus"] == 91.0          # plain mean of 90 and 92


def test_weights_pull_consensus_toward_skilled_model():
    day = date(2030, 7, 1)
    series = {"det_gfs_seamless": _member(day, 90.0),
              "det_gem_seamless": _member(day, 96.0)}
    calib = {"weights": {"high": {"det_gfs_seamless": 0.9, "det_gem_seamless": 0.1}}}
    out = model.predict_variable(series, {"obs": ([], [])}, day, "high", None, calib)
    # weighted mean = 0.9*90 + 0.1*96 = 90.6
    assert out["consensus"] == 90.6


def test_system_weights_shrink_toward_equal_and_favor_skill():
    # 'good' system nails the actual; 'bad' is 4 off, every day.
    d0 = date(2026, 5, 1)
    ext, actual = {}, {}
    for i in range(40):
        d = d0 + timedelta(days=i)
        actual[d] = (90.0, 70.0)
        ext[d] = {"good": {"high": 90.0, "low": 70.0},
                  "bad": {"high": 94.0, "low": 74.0}}
    w = calibration._system_weights(ext, actual, ["good", "bad"], lam=0.25)
    # high: good must outweigh bad, but shrinkage keeps both within [0.2, 0.8]
    assert w["high"]["good"] > w["high"]["bad"]
    assert 0.2 < w["high"]["good"] < 0.8
    assert abs(w["high"]["good"] + w["high"]["bad"] - 1.0) < 1e-9


def test_system_weights_equal_when_skill_is_equal():
    d0 = date(2026, 5, 1)
    ext, actual = {}, {}
    for i in range(40):
        d = d0 + timedelta(days=i)
        actual[d] = (90.0, 70.0)
        # both systems equally (un)skilled: symmetric errors
        ext[d] = {"a": {"high": 91.0, "low": 71.0},
                  "b": {"high": 89.0, "low": 69.0}}
    w = calibration._system_weights(ext, actual, ["a", "b"], lam=0.25)
    assert abs(w["high"]["a"] - w["high"]["b"]) < 1e-6


def test_gate_keeps_weights_only_when_they_beat_equal():
    # 'good' nails the actual, 'bad' is 5 off, every day -> walk-forward weights
    # learned on the trailing window favor 'good' and beat equal on held-out days.
    d0 = date(2026, 5, 1)
    ext, actual = {}, {}
    for i in range(40):
        d = d0 + timedelta(days=i)
        actual[d] = (90.0, 70.0)
        ext[d] = {"good": {"high": 90.0, "low": 70.0},
                  "bad": {"high": 95.0, "low": 75.0}}
    systems = ["good", "bad"]
    assert calibration._weights_beat_equal(ext, actual, systems, "high",
                                           margin=0.02) is True


def test_gate_rejects_when_no_improvement():
    # symmetric, equal skill -> learned weights stay equal -> no OOS improvement.
    d0 = date(2026, 5, 1)
    ext, actual = {}, {}
    for i in range(40):
        d = d0 + timedelta(days=i)
        actual[d] = (90.0, 70.0)
        ext[d] = {"a": {"high": 91.0, "low": 71.0},
                  "b": {"high": 89.0, "low": 69.0}}   # symmetric, equal skill
    systems = ["a", "b"]
    assert calibration._weights_beat_equal(ext, actual, systems, "high",
                                           margin=0.02) is False


def test_backtest_uses_system_weights_when_provided(monkeypatch):
    day = date(2026, 6, 10)
    det = {"det_gfs_seamless": _member(day, 90.0),
           "det_gem_seamless": _member(day, 96.0)}
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e: det)
    monkeypatch.setattr(open_meteo_ensemble, "fetch_historical", lambda s, e: {})
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {day: (90.0, 75.0)})
    monkeypatch.setattr(calibration, "get", lambda refresh=True: {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0},
        "weights": {"high": {"det_gfs_seamless": 0.9, "det_gem_seamless": 0.1},
                    "low": {"det_gfs_seamless": 0.5, "det_gem_seamless": 0.5}}})
    res = backtest.run()
    # weighted high consensus = 0.9*90 + 0.1*96 = 90.6 -> MAE vs 90 = 0.6
    assert res["high"]["mae"] == 0.6
