from datetime import date, datetime
from zoneinfo import ZoneInfo

import model
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _stub_snapshot_deps(monkeypatch, now):
    """Freeze the clock and stub the fetch layer so snapshot() is pure."""
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(model, "datetime", _FakeDT)
    monkeypatch.setattr(model, "gather_series",
                        lambda **k: ({}, {"obs": ([], [])}, []))
    monkeypatch.setattr(model, "_predict_from",
                        lambda series, obs, day, *a, **k: {"day": day.isoformat(),
                                                          "high": {"consensus": 99.0},
                                                          "low": {"consensus": 79.0}})
    monkeypatch.setattr(model, "per_source_extremes", lambda series, day: {})
    monkeypatch.setattr(model, "_storm_status", lambda t, n: None)


def test_yesterday_block_present_in_the_final_hour(monkeypatch):
    now = datetime(2026, 7, 20, 0, 30, tzinfo=_TZ)
    _stub_snapshot_deps(monkeypatch, now)
    snap = model.snapshot()
    assert snap["yesterday"]["day"] == "2026-07-19"
    assert snap["today"]["day"] == "2026-07-20"
    assert "yesterday" in snap["sources"]


def test_no_yesterday_block_during_the_day(monkeypatch):
    now = datetime(2026, 7, 20, 15, 0, tzinfo=_TZ)
    _stub_snapshot_deps(monkeypatch, now)
    snap = model.snapshot()
    assert "yesterday" not in snap
    assert "yesterday" not in snap["sources"]


def test_no_yesterday_block_after_the_boundary(monkeypatch):
    now = datetime(2026, 7, 20, 1, 5, tzinfo=_TZ)      # July 19 has settled
    _stub_snapshot_deps(monkeypatch, now)
    assert "yesterday" not in model.snapshot()


def test_no_yesterday_block_in_winter(monkeypatch):
    now = datetime(2026, 1, 6, 0, 30, tzinfo=_TZ)      # CST: no gap exists
    _stub_snapshot_deps(monkeypatch, now)
    assert "yesterday" not in model.snapshot()
