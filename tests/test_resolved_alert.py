"""Per-variable 70%-resolved ntfy alert in scheduled_log."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
import scheduled_log

_TZ = ZoneInfo(TIMEZONE)


def _snap(high_res, low_res, high_c=97.0, low_c=80.0):
    def _v(res, c):
        return {"resolved": res, "consensus": c,
                "convective_widened": False, "front_widened": False}
    return {"today": {"high": _v(high_res, high_c), "low": _v(low_res, low_c)}}


def _patch(monkeypatch, tmp_path, sends):
    monkeypatch.setattr(scheduled_log, "RESOLVED_STATE_PATH",
                        str(tmp_path / "resolved.json"))
    import notify
    monkeypatch.setattr(notify, "send_ntfy",
                        lambda title, message: sends.append((title, message)) or True)


_NOW = datetime(2026, 7, 21, 15, 0, tzinfo=_TZ)


def test_fires_at_70_not_69(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    scheduled_log._maybe_alert_resolved(_snap(0.695, 0.60), _NOW)  # 69% / 60%
    assert sends == []
    scheduled_log._maybe_alert_resolved(_snap(0.70, 0.60), _NOW)   # 70% / 60%
    assert len(sends) == 1
    assert sends[0][0] == "Dallas High locking in"
    assert "70% resolved" in sends[0][1] and "97" in sends[0][1]


def test_high_and_low_independent(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    scheduled_log._maybe_alert_resolved(_snap(0.85, 0.50), _NOW)  # only high ≥70
    assert [t for t, _ in sends] == ["Dallas High locking in"]
    scheduled_log._maybe_alert_resolved(_snap(0.90, 0.75), _NOW)  # low now ≥70
    assert [t for t, _ in sends] == ["Dallas High locking in", "Dallas Low locking in"]


def test_once_per_day_then_rearms(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    scheduled_log._maybe_alert_resolved(_snap(0.80, 0.80), _NOW)
    scheduled_log._maybe_alert_resolved(_snap(0.95, 0.95), _NOW)  # same day
    assert len(sends) == 2  # one high + one low, no repeats
    tomorrow = datetime(2026, 7, 22, 15, 0, tzinfo=_TZ)
    scheduled_log._maybe_alert_resolved(_snap(0.80, 0.80), tomorrow)
    assert len(sends) == 4  # re-armed next day


def test_empty_state_file_does_not_block(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    (tmp_path / "resolved.json").write_text("")  # 0-byte restore artifact
    scheduled_log._maybe_alert_resolved(_snap(0.80, 0.50), _NOW)
    assert len(sends) == 1
