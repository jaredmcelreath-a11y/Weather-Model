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


def _mixture_stats(probs):
    """Weighted mean/std of a PMF over its integer bins (open tails ignored)."""
    pts = [(int(k), p) for k, p in probs.items() if not k.startswith(("<", ">"))]
    w = sum(p for _, p in pts) or 1.0
    mean = sum(t * p for t, p in pts) / w
    var = sum(p * (t - mean) ** 2 for t, p in pts) / w
    return mean, var ** 0.5


def test_variance_pinning_rescale_capped_no_phantom_tail():
    # The live 2026-07-18 artifact: obs-anchored members cluster tightly while
    # two tiny-weight ensemble members sit a couple °F cooler. Uncapped, the
    # variance-pinning rescale (alpha = sqrt(needed/raw_var)) flings those
    # members ~20°F into the tail, printing a phantom mode (a 59°F bin on a
    # 78°F night). The rescale must be capped, with the residual variance
    # absorbed by the kernel bandwidth instead.
    samples = [78.98] * 7 + [78.98] * 29 + [77.0, 76.5]
    weights = [0.15] * 7 + [0.15 / 31] * 31
    probs = model._bin_probabilities(samples, 0.9, weights)
    mean, std = _mixture_stats(probs)
    # No meaningful mass far below the cluster (was ~0.001 at 70-73 uncapped).
    far_tail = sum(p for k, p in probs.items()
                   if not k.startswith(("<", ">")) and int(k) < mean - 6)
    assert far_tail < 1e-4
    # The spread is still pinned to the target sigma.
    assert abs(std - 0.9) < 0.1


def test_variance_pinning_unchanged_when_spread_is_normal():
    # A normally-spread sample set never hits the cap, so behavior (and the
    # exact variance pinning) is identical to the historical rescale.
    samples = [88.0, 90.0, 92.0]
    probs = model._bin_probabilities(samples, 2.0)
    mean, std = _mixture_stats(probs)
    assert abs(mean - 90.0) < 0.05
    assert abs(std - 2.0) < 0.1


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


def _ens_ext(n, ens_off, det_off):
    """n days where ensemble_mean is `ens_off` from actual and the det models are
    `det_off` from actual (actual fixed at high 90 / low 70)."""
    d0 = date(2026, 5, 1)
    ext, actual = {}, {}
    for i in range(n):
        d = d0 + timedelta(days=i)
        actual[d] = (90.0, 70.0)
        ext[d] = {
            "ensemble_mean": {"high": 90.0 + ens_off, "low": 70.0 + ens_off},
            "det_gfs_seamless": {"high": 90.0 + det_off, "low": 70.0 + det_off},
            "det_ecmwf_ifs025": {"high": 90.0 + det_off, "low": 70.0 + det_off},
        }
    return ext, actual


def test_ens_bias_gate_fires_when_ensemble_bias_differs():
    # Ensemble runs +3 hot, deterministic +0.5: the ensemble's own bias de-centers
    # it far better than the copied deterministic bias, out-of-sample.
    ext, actual = _ens_ext(40, ens_off=3.0, det_off=0.5)
    assert calibration._ens_bias_beats_copied(ext, actual, "high") is True
    assert round(calibration._system_bias(ext, actual, "ensemble_mean", "high"), 2) == 3.0


def test_ens_bias_gate_rejects_when_biases_match():
    # Ensemble and deterministic share the same bias -> copying is already optimal,
    # so the gate must NOT fire (no OOS win).
    ext, actual = _ens_ext(40, ens_off=1.0, det_off=1.0)
    assert calibration._ens_bias_beats_copied(ext, actual, "high") is False


def test_ens_bias_gate_rejects_on_thin_archive():
    # Mirrors the live constraint: too few ensemble days to clear the OOS bar.
    ext, actual = _ens_ext(5, ens_off=3.0, det_off=0.5)
    assert calibration._ens_bias_beats_copied(ext, actual, "high") is False


def test_exact_bin_sigma_tightens_when_concentrated():
    # Models nail the actual every day -> the exact bin is hit at any sigma, so the
    # gate tightens to the model floor (1.0) rather than the wide residual (2.0).
    d0 = date(2026, 5, 1)
    fcst, actual = {}, {}
    for i in range(40):
        d = d0 + timedelta(days=i)
        a = 90 + (i % 5)
        actual[d] = (a, a - 20)
        fcst[d] = {"high": [a, a, a], "low": [a - 20, a - 20, a - 20]}
    out = calibration._exact_bin_sigma(fcst, actual, 0.0, "high", residual_sigma=2.0)
    assert out is not None and out < 2.0
    assert out == 1.0   # _MIN_SIGMA floor


def test_exact_bin_sigma_falls_back_on_thin_data():
    d0 = date(2026, 5, 1)
    fcst, actual = {}, {}
    for i in range(10):                       # < 20 -> not enough to validate
        d = d0 + timedelta(days=i)
        actual[d] = (90, 70)
        fcst[d] = {"high": [90], "low": [70]}
    assert calibration._exact_bin_sigma(fcst, actual, 0.0, "high",
                                        residual_sigma=2.0) == 2.0


def test_exact_bin_sigma_noop_when_residual_at_floor():
    # Residual already <= _MIN_SIGMA: nothing to tighten, return unchanged.
    assert calibration._exact_bin_sigma({}, {}, 0.0, "high", residual_sigma=0.9) == 0.9


def test_backtest_uses_system_weights_when_provided(monkeypatch):
    day = date(2026, 6, 10)
    det = {"det_gfs_seamless": _member(day, 90.0),
           "det_gem_seamless": _member(day, 96.0)}
    monkeypatch.setattr(open_meteo_models, "fetch_historical", lambda s, e, **kw: det)
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


def test_sample_weights_routes_mos_to_own_weight():
    series = {"ens_a": None, "det_gfs_seamless": None,
              "mos_lav": None, "mos_nbs": None, "nws_x": None}
    weights = {"ensemble_mean": 0.2, "det_gfs_seamless": 0.2,
               "mos_lav": 0.1, "mos_nbs": 0.4, "nws": 0.1}
    w = model._sample_weights(series, weights)
    assert abs(w["mos_nbs"] - 0.4) < 1e-9      # its own skill weight, not nws
    assert abs(w["mos_lav"] - 0.1) < 1e-9
    assert abs(w["nws_x"] - 0.1) < 1e-9        # nws still keys 'nws'


def test_sample_weights_mos_falls_back_to_avg_when_absent():
    series = {"det_gfs_seamless": None, "mos_nbs": None}
    weights = {"det_gfs_seamless": 0.5, "nws": 0.5}   # no mos_nbs key
    w = model._sample_weights(series, weights)
    avg = sum(weights.values()) / len(weights)        # 0.5
    assert abs(w["mos_nbs"] - avg) < 1e-9
