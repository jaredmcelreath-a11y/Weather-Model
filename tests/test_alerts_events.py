"""alerts.maybe_fire_events — triggers, once-per-day gating, independence."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
import alerts

_TZ = ZoneInfo(TIMEZONE)


def _snap(level="clear", front=False, sigma=3.0):
    return {
        "storm": {"level": level, "sigma": sigma,
                  "upstream": {"active": level == "active",
                               "county": "Tarrant", "direction": "NW"}},
        "today": {"low": {"consensus": 80.0, "front_widened": front,
                          "front_guard": {"projection": 77.0}}},
    }


def _patch(monkeypatch, tmp_path, sends, recap="Morning digest"):
    monkeypatch.setattr(alerts, "EVENT_STATE_PATH", str(tmp_path / "ev.json"))
    monkeypatch.setattr(alerts, "_build_recap_body", lambda snap: recap)
    monkeypatch.setattr(alerts.notify, "send_ntfy",
                        lambda title, body: sends.append((title, body)) or True)


# 3 PM local — past the recap window, so recap fires too unless gated out.
_PM = datetime(2026, 7, 21, 15, 0, tzinfo=_TZ)


def test_storm_fires_on_active_not_watch(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends, recap="")  # suppress recap
    alerts.maybe_fire_events(_snap(level="watch"), _PM)
    assert not any(t == "Storm Watch Active" for t, _ in sends)
    alerts.maybe_fire_events(_snap(level="active"), _PM)
    assert any(t == "Storm Watch Active" for t, _ in sends)


def test_front_fires_only_when_widened(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends, recap="")
    alerts.maybe_fire_events(_snap(front=False), _PM)
    assert not any(t == "Front Risk" for t, _ in sends)
    alerts.maybe_fire_events(_snap(front=True), _PM)
    assert [t for t, _ in sends] == ["Front Risk"]


def test_recap_time_gate_and_once_per_day(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    before = datetime(2026, 7, 21, 6, 0, tzinfo=_TZ)
    alerts.maybe_fire_events(_snap(), before)          # 06:00 — too early
    assert not any(t == "Morning Recap" for t, _ in sends)
    at = datetime(2026, 7, 21, 6, 30, tzinfo=_TZ)
    alerts.maybe_fire_events(_snap(), at)              # 06:30 — fires
    alerts.maybe_fire_events(_snap(), _PM)             # later same day — quiet
    assert [t for t, _ in sends].count("Morning Recap") == 1
    tomorrow = datetime(2026, 7, 22, 6, 35, tzinfo=_TZ)
    alerts.maybe_fire_events(_snap(), tomorrow)        # re-arms
    assert [t for t, _ in sends].count("Morning Recap") == 2


def test_all_three_independent_same_run(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    alerts.maybe_fire_events(_snap(level="active", front=True), _PM)
    titles = sorted(t for t, _ in sends)
    assert titles == ["Front Risk", "Morning Recap", "Storm Watch Active"]


def test_empty_state_file_does_not_block(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends, recap="")
    (tmp_path / "ev.json").write_text("")
    alerts.maybe_fire_events(_snap(level="active"), _PM)
    assert [t for t, _ in sends] == ["Storm Watch Active"]
