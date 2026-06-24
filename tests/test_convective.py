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
    assert config.CONVECTIVE_POP_MIN > 0
    assert config.CONVECTIVE_CAPE_MIN > 0
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


def test_point_triggered():
    from convective import _point_triggered
    assert _point_triggered(40, 100, pop_min=30, cape_min=1000) is True   # POP over
    assert _point_triggered(10, 1500, pop_min=30, cape_min=1000) is True  # CAPE over
    assert _point_triggered(10, 100, pop_min=30, cape_min=1000) is False  # both under
    assert _point_triggered(None, None, pop_min=30, cape_min=1000) is False


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
