"""Once-per-day CLI push gate in scheduled_log."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
import scheduled_log

_TZ = ZoneInfo(TIMEZONE)


def _cli(day):
    return {
        "report_date": day, "high_f": 100, "low_f": 80,
        "high_time": "254 PM", "low_time": "615 AM",
        "issued": datetime(day.year, day.month, day.day, 16, 41, tzinfo=_TZ),
    }


def _patch(monkeypatch, tmp_path, cli, sends):
    monkeypatch.setattr(scheduled_log, "STATE_PATH", str(tmp_path / "state.json"))
    from sources import nws_cli
    import notify
    monkeypatch.setattr(nws_cli, "fetch_latest_cli", lambda ttl=None: cli)

    def fake_send(title, message):
        sends.append((title, message))
        return True

    monkeypatch.setattr(notify, "send_ntfy", fake_send)


def test_alerts_once_per_day(monkeypatch, tmp_path):
    day = date(2026, 7, 20)
    now = datetime(2026, 7, 20, 16, 45, tzinfo=_TZ)
    sends = []
    _patch(monkeypatch, tmp_path, _cli(day), sends)

    scheduled_log._maybe_alert_cli(now)
    scheduled_log._maybe_alert_cli(now)  # later cron run, same day
    assert len(sends) == 1
    assert sends[0][0] == "Dallas Climate Report"
    assert "100" in sends[0][1] and "80" in sends[0][1]


def test_no_alert_when_report_is_not_today(monkeypatch, tmp_path):
    now = datetime(2026, 7, 20, 6, 50, tzinfo=_TZ)
    sends = []
    _patch(monkeypatch, tmp_path, _cli(date(2026, 7, 19)), sends)  # prior-day product
    scheduled_log._maybe_alert_cli(now)
    assert sends == []


def test_next_day_alerts_again(monkeypatch, tmp_path):
    sends = []
    # Day 1
    _patch(monkeypatch, tmp_path, _cli(date(2026, 7, 20)), sends)
    scheduled_log._maybe_alert_cli(datetime(2026, 7, 20, 16, 45, tzinfo=_TZ))
    # Day 2 — same state file, new report
    _patch(monkeypatch, tmp_path, _cli(date(2026, 7, 21)), sends)
    scheduled_log._maybe_alert_cli(datetime(2026, 7, 21, 16, 45, tzinfo=_TZ))
    assert len(sends) == 2
