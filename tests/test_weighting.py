"""Skill-weighted, group-rebalanced consensus."""
from datetime import date

import model
from sources import open_meteo_ensemble
from sources import common


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


from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


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
