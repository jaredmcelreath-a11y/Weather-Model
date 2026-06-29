"""Tests for the convective downside-humility trigger and the model sigma gate.
All synthetic — no live network — mirroring tests/test_accuracy.py.
"""

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
from config import TIMEZONE

TZ = ZoneInfo(TIMEZONE)
DAY = date(2026, 6, 16)


def test_convective_config_constants():
    assert config.CONVECTIVE_SIGMA >= 2.0
    # The just-armed floor is positive and below the full storm-day floor.
    assert 0 < config.CONVECTIVE_SIGMA_MIN < config.CONVECTIVE_SIGMA
    # POP arms below the level that earns the full downside.
    assert 0 < config.CONVECTIVE_POP_MIN < config.CONVECTIVE_POP_FULL
    ugc = set(config.CONVECTIVE_UPSTREAM_UGC)
    assert "TXC497" in ugc  # Wise County — the NW approach


def test_window_max_reduces_to_remaining_hours():
    from sources.open_meteo_models import _window_max
    base = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    times = [base + timedelta(hours=h) for h in range(24)]
    pop = [float(h) for h in range(24)]          # 0..23, increasing
    cape = [100.0 * h for h in range(24)]         # 0..2300
    now = datetime(DAY.year, DAY.month, DAY.day, 18, tzinfo=TZ)
    mp, mc = _window_max(times, pop, cape, DAY, now)
    assert mp == 23.0 and mc == 2300.0            # max over [18:00, midnight)


def test_window_max_empty_window_is_none():
    from sources.open_meteo_models import _window_max
    base = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    times = [base + timedelta(hours=h) for h in range(5)]   # only 00:00-04:00
    now = datetime(DAY.year, DAY.month, DAY.day, 18, tzinfo=TZ)
    mp, mc = _window_max(times, [1.0] * 5, [1.0] * 5, DAY, now)
    assert mp is None and mc is None


def test_fetch_active_returns_data_on_success(monkeypatch):
    from sources import nws_alerts, common
    payload = {"features": [{"properties": {"event": "Heat Advisory"}}]}
    monkeypatch.setattr(common, "get_json", lambda *a, **k: payload)
    assert nws_alerts.fetch_active() == payload


def test_fetch_active_returns_empty_on_error(monkeypatch):
    from sources import nws_alerts, common

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(common, "get_json", boom)
    assert nws_alerts.fetch_active() == {"features": []}


def test_point_triggered_requires_pop():
    # POP is the gate; CAPE alone (latent instability with no precip expected)
    # must NOT arm — that was the false-positive that spread every hot day's low.
    from convective import _point_triggered
    assert _point_triggered(40, pop_min=30) is True    # POP over
    assert _point_triggered(10, pop_min=30) is False   # POP under (CAPE irrelevant)
    assert _point_triggered(None, pop_min=30) is False


def test_point_sigma_scales_with_pop():
    from convective import _point_sigma
    lo, hi = config.CONVECTIVE_SIGMA_MIN, config.CONVECTIVE_SIGMA
    pmin, pfull = config.CONVECTIVE_POP_MIN, config.CONVECTIVE_POP_FULL
    assert _point_sigma(pmin - 1) == 0.0                 # below arming -> nothing
    assert _point_sigma(pmin) == lo                       # just armed -> the floor
    assert _point_sigma(pfull) == hi                      # near-certain -> full
    assert _point_sigma((pmin + pfull) / 2) == (lo + hi) / 2  # linear in between
    assert _point_sigma(pfull + 50) == hi                # clamped at full
    # monotonic: more POP, never less downside
    assert _point_sigma(40) < _point_sigma(60)


def test_cape_alone_no_longer_widens():
    # The reported bug: high CAPE with zero POP and no upstream warning used to
    # floor the locked low at 3 sigma. It must now contribute nothing.
    import convective
    from sources import nws_alerts, open_meteo_models
    now = datetime(DAY.year, DAY.month, DAY.day, 14, tzinfo=TZ)
    convective.open_meteo_models.convective_window  # ensure attr exists
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(open_meteo_models, "convective_window", lambda d, n: (0.0, 1820.0))
    mp.setattr(nws_alerts, "fetch_active", lambda: {"features": []})
    try:
        assert convective.convective_sigma(DAY, now) == 0.0
        assert convective.convective_risk(DAY, now) is False
    finally:
        mp.undo()


def test_upstream_triggered():
    from convective import _upstream_triggered
    zones = frozenset({"TXC497", "TXC237"})
    svr = {"features": [{"properties": {
        "event": "Severe Thunderstorm Warning",
        "geocode": {"UGC": ["TXC497", "TXC367"]}}}]}
    assert _upstream_triggered(svr, zones) is True
    # right counties, wrong event
    flood = {"features": [{"properties": {
        "event": "Flood Warning", "geocode": {"UGC": ["TXC497"]}}}]}
    assert _upstream_triggered(flood, zones) is False
    # right event, counties outside the approach set
    far = {"features": [{"properties": {
        "event": "Severe Thunderstorm Warning", "geocode": {"UGC": ["TXC999"]}}}]}
    assert _upstream_triggered(far, zones) is False
    assert _upstream_triggered({}, zones) is False


def test_risk_label():
    from convective import risk_label
    assert risk_label({"convective_widened": True}) is not None
    assert risk_label({"convective_widened": False}) is None
    assert risk_label({}) is None


def test_convective_sigma_combines_signals_and_is_best_effort(monkeypatch):
    import convective
    from sources import nws_alerts, open_meteo_models
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)
    no_alerts = {"features": []}
    one_zone = list(convective.UPSTREAM_UGC)[0]
    svr = {"features": [{"properties": {
        "event": "Severe Thunderstorm Warning", "geocode": {"UGC": [one_zone]}}}]}

    # point POP alone scales the downside (50% POP -> a partial floor)
    monkeypatch.setattr(open_meteo_models, "convective_window", lambda d, n: (50.0, 200.0))
    monkeypatch.setattr(nws_alerts, "fetch_active", lambda: no_alerts)
    s = convective.convective_sigma(DAY, now)
    assert 0 < s < config.CONVECTIVE_SIGMA
    assert convective.convective_risk(DAY, now) is True

    # an upstream severe warning commands the full floor even with quiet point POP
    monkeypatch.setattr(open_meteo_models, "convective_window", lambda d, n: (0.0, 0.0))
    monkeypatch.setattr(nws_alerts, "fetch_active", lambda: svr)
    assert convective.convective_sigma(DAY, now) == config.CONVECTIVE_SIGMA

    # neither -> no downside
    monkeypatch.setattr(nws_alerts, "fetch_active", lambda: no_alerts)
    assert convective.convective_sigma(DAY, now) == 0.0
    assert convective.convective_risk(DAY, now) is False

    # any exception -> 0 (best-effort, never raises)
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(open_meteo_models, "convective_window", boom)
    monkeypatch.setattr(nws_alerts, "fetch_active", boom)
    assert convective.convective_sigma(DAY, now) == 0.0


def _locked_low_inputs():
    """Obs V-shape: low 79 at 05:00, risen to 90 by 16:00 (low locked), plus
    three full-day forecast members with mins straddling 79."""
    base = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ)
    ot = [base + timedelta(hours=h) for h in range(17)]
    ov = [79 + abs(h - 5) for h in range(17)]          # 84..79..90
    ftimes = [base + timedelta(hours=h) for h in range(24)]
    fc = {f"det_{i}": (ftimes, [79 + m + abs(h - 5) for h in range(24)])
          for i, m in enumerate((-1, 0, 1))}
    return fc, {"obs": (ot, ov)}, base


def test_convective_widens_locked_low(monkeypatch):
    import model
    fc, obs, base = _locked_low_inputs()
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)

    monkeypatch.setattr(model, "convective_sigma", lambda day, now: 0.0)
    off = model.predict_variable(fc, obs, DAY, "low", now, None, live=True)
    monkeypatch.setattr(model, "convective_sigma", lambda day, now: config.CONVECTIVE_SIGMA)
    on = model.predict_variable(fc, obs, DAY, "low", now, None, live=True)

    # sanity: the low is locked in both runs
    assert off["peak_locked"] and on["peak_locked"]
    # the flag is set only when risk is live
    assert on["convective_widened"] and not off["convective_widened"]
    # confidence loosens: spread widens to the convective floor
    assert on["sigma_used"] > off["sigma_used"]
    assert on["sigma_used"] >= config.CONVECTIVE_SIGMA - 1e-9
    # one-sided: zero mass above the observed low (79) either way
    assert model.prob_at_least(on["probabilities"], 80) < 1e-9
    assert model.prob_at_least(off["probabilities"], 80) < 1e-9
    # real downside mass appears at/below 77
    assert model.prob_at_most(on["probabilities"], 77) > model.prob_at_most(off["probabilities"], 77)
    # consensus (mean) is unchanged — only spread moved
    assert on["consensus"] == off["consensus"]


def test_convective_does_not_touch_high(monkeypatch):
    import model
    fc, obs, base = _locked_low_inputs()
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)
    monkeypatch.setattr(model, "convective_sigma", lambda day, now: config.CONVECTIVE_SIGMA)
    hi_on = model.predict_variable(fc, obs, DAY, "high", now, None, live=True)
    monkeypatch.setattr(model, "convective_sigma", lambda day, now: 0.0)
    hi_off = model.predict_variable(fc, obs, DAY, "high", now, None, live=True)
    assert hi_on["probabilities"] == hi_off["probabilities"]
    assert hi_on["convective_widened"] is False


def test_convective_no_op_when_not_live(monkeypatch):
    # The default (non-live) path — what backtest/replay uses — must never call
    # convective_sigma, even for today's low.
    import model
    fc, obs, base = _locked_low_inputs()
    now = datetime(DAY.year, DAY.month, DAY.day, 16, tzinfo=TZ)

    def boom(day, now):
        raise AssertionError("convective_sigma must not run when live=False")

    monkeypatch.setattr(model, "convective_sigma", boom)
    out = model.predict_variable(fc, obs, DAY, "low", now, None)   # live defaults False
    assert out["convective_widened"] is False


def test_risk_label_matches_model_flag():
    # End-to-end glue: a low prediction with the flag set yields a caption; a
    # plain one yields nothing. Guards against the panel reading the wrong key.
    from convective import risk_label
    assert risk_label({"convective_widened": True, "consensus": 77}) is not None
    assert risk_label({"convective_widened": False, "consensus": 77}) is None
