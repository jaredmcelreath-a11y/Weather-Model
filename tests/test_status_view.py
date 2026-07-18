"""Status page threshold logic: every check is a pure function of plain
timestamps/counts, so green/amber/red boundaries are unit-testable."""
import sys
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import status_view

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)


def _card(cards, label):
    return next(c for c in cards if c["label"] == label)


def test_heartbeat_thresholds():
    for mins, state in ((10, "green"), (40, "amber"), (90, "red")):
        cards = status_view.checks(
            {"last_capture": NOW - timedelta(minutes=mins)}, NOW)
        assert _card(cards, "Action Heartbeat")["state"] == state


def test_obs_and_calibration_thresholds():
    cards = status_view.checks(
        {"obs_time": NOW - timedelta(minutes=100),
         "calib_computed": NOW - timedelta(hours=40)}, NOW)
    assert _card(cards, "Obs Reading")["state"] == "red"
    assert _card(cards, "Calibration")["state"] == "amber"


def test_feeds_states():
    assert _card(status_view.checks({"dropped_sources": []}, NOW),
                 "Forecast Feeds")["state"] == "green"
    assert _card(status_view.checks({"dropped_sources": ["nws"]}, NOW),
                 "Forecast Feeds")["state"] == "amber"
    assert _card(status_view.checks({"dropped_sources": ["nws", "gem"]}, NOW),
                 "Forecast Feeds")["state"] == "red"


def test_settlements_and_betting_log():
    cards = status_view.checks(
        {"last_settled": date(2026, 7, 17), "betting_rows_today": 6}, NOW)
    assert _card(cards, "Settlements")["state"] == "green"
    assert _card(cards, "Betting Log")["state"] == "green"
    cards = status_view.checks(
        {"last_settled": date(2026, 7, 14), "betting_rows_today": 0}, NOW)
    assert _card(cards, "Settlements")["state"] == "red"
    assert _card(cards, "Betting Log")["state"] == "red"


def test_missing_inputs_read_unknown():
    cards = status_view.checks({}, NOW)
    assert all(c["state"] == "unknown" for c in cards)
    assert all(c["value"] == "No Data" for c in cards)


def test_render_smoke_with_and_without_snapshot():
    snap = {"current": {"temp": 93.0, "time": "2026-07-18T11:55-05:00"},
            "dropped_sources": []}
    inputs = {"last_capture": NOW - timedelta(minutes=8),
              "last_settled": date(2026, 7, 17), "betting_rows_today": 4}
    status_view.render(snap, inputs, {"Forecast Log": 170, "Settlements": 64})
    status_view.render(None, {}, {})


def test_snapshot_inputs_extraction():
    snap = {"current": {"temp": 93.0, "time": "2026-07-18T11:55-05:00"},
            "dropped_sources": ["gem"]}
    inputs = status_view.snapshot_inputs(snap)
    assert inputs["dropped_sources"] == ["gem"]
    assert inputs["obs_time"].tzinfo is not None
    assert status_view.snapshot_inputs(None) == {}
