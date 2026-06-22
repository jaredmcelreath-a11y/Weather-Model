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
