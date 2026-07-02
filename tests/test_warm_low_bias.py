"""Warm-night low bias: gated measurement + fallbacks."""
from datetime import date, timedelta

from calibration import _warm_low_bias


def _mk(pairs):
    """Build (fcst, actual) over consecutive days from (consensus_low, actual_low)."""
    fcst, actual = {}, {}
    d = date(2026, 5, 1)
    for cons, act in pairs:
        fcst[d] = {"high": [95.0], "low": [cons]}
        actual[d] = (95.0, act)
        d += timedelta(days=1)
    return fcst, actual


def test_emits_warm_low_bias_when_warm_nights_run_cold():
    # 12 warm nights (fc 78) verifying ~1.0 warmer (cold lean ~-1.0, small noise)
    # + 15 neutral cool nights. overall low bias = -12/27 = -0.444.
    pairs = [(78.0, 79.2), (78.0, 78.8)] * 6 + [(70.0, 70.0)] * 15
    fcst, actual = _mk(pairs)
    out = _warm_low_bias(fcst, actual, -0.444, threshold=76)
    assert out["threshold"] == 76
    # warm mean residual -1.0; warm_extra = -1.0 - (-0.444) = -0.556;
    # shrink *12/(12+8) -> -0.3336 -> round -0.33
    assert out["bias"] == -0.33


def test_none_when_too_few_warm_nights():
    pairs = [(78.0, 79.0)] * 9 + [(70.0, 70.0)] * 15      # only 9 warm (< 10)
    fcst, actual = _mk(pairs)
    assert _warm_low_bias(fcst, actual, -0.2, threshold=76) == {}


def test_none_when_no_extra_lean_beyond_flat_bias():
    # every night runs -0.5; warm mean residual == overall -> warm_extra 0 -> {}.
    pairs = [(78.0, 78.5)] * 12 + [(70.0, 70.5)] * 15
    fcst, actual = _mk(pairs)
    assert _warm_low_bias(fcst, actual, -0.5, threshold=76) == {}


def test_none_when_warm_lean_insignificant():
    # warm residuals -3.2 / +3.0 alternating: mean -0.1, sigma ~3.1 -> fails sig.
    pairs = [(78.0, 81.2), (78.0, 75.0)] * 6 + [(70.0, 70.0)] * 15
    fcst, actual = _mk(pairs)
    assert _warm_low_bias(fcst, actual, 0.0, threshold=76) == {}


from datetime import datetime
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)

_CALIB_WARM = {
    "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
    "sigma": {"high": 2.0, "low": 2.0},
    "bias_correction": {"warm_low": {"threshold": 76, "bias": -0.5}},
}


def _member(day, peak):
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    temps = [peak - abs(h - 15) for h in range(24)]   # max=peak, min=peak-15
    return times, temps


def _series(day, peaks=(92.0, 94.0)):
    return {f"det_{i}": _member(day, p) for i, p in enumerate(peaks)}


def test_model_warms_low_on_warm_night():
    day = date(2030, 7, 1)
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, _CALIB_WARM)
    # lows 77,79 -> consensus 78 >= 76 -> subtract -0.5 -> +0.5 -> 78.5
    assert out["consensus"] == 78.5


def test_model_leaves_cool_night_low():
    day = date(2030, 7, 1)
    out = model.predict_variable(_series(day, peaks=(88.0, 90.0)), {"obs": ([], [])},
                                 day, "low", None, _CALIB_WARM)
    # lows 73,75 -> consensus 74 < 76 -> no correction
    assert out["consensus"] == 74.0


def test_model_never_touches_high():
    day = date(2030, 7, 1)
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "high",
                                 None, _CALIB_WARM)
    assert out["consensus"] == 93.0            # mean(92,94), untouched


def test_model_skips_warm_low_when_obs_anchored():
    day = date.today()
    base = datetime(day.year, day.month, day.day, tzinfo=_TZ)
    now = base + timedelta(hours=8)
    obs_times = [base + timedelta(hours=h) for h in range(9)]
    obs_temps = [82.0 - h * 0.4 for h in range(9)]      # warm morning, min ~78.8
    obs = {"obs": (obs_times, obs_temps)}
    warm = model.predict_variable(_series(day), obs, day, "low", now, _CALIB_WARM)
    plain_calib = dict(_CALIB_WARM, bias_correction={})
    plain = model.predict_variable(_series(day), obs, day, "low", now, plain_calib)
    # obs anchor the day -> correction skipped -> identical to no-knob run
    assert warm["consensus"] == plain["consensus"]


def test_model_warm_low_and_cooling_stack(monkeypatch):
    day = date(2030, 7, 1)
    monkeypatch.setattr(model.open_meteo_models, "night_conditions",
                        lambda d: (10.0, 5.0))          # clear + calm
    calib = {
        "bias": {"deterministic": {"high": 0.0, "low": 0.0}},
        "sigma": {"high": 2.0, "low": 2.0},
        "cooling": {"cloud_thresh": 30, "wind_thresh": 10, "low_offset": 0.2},
        "bias_correction": {"warm_low": {"threshold": 76, "bias": -0.5}},
    }
    out = model.predict_variable(_series(day), {"obs": ([], [])}, day, "low",
                                 None, calib)
    # cooling -0.2 then warm_low +0.5: 78 - 0.2 + 0.5 = 78.3
    assert out["consensus"] == 78.3
