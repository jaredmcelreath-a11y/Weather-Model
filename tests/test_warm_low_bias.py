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
