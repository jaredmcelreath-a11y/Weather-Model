from datetime import date, datetime
from zoneinfo import ZoneInfo

import scheduled_log
from config import TIMEZONE
from sources import kalshi

_TZ = ZoneInfo(TIMEZONE)


def test_ask_rows_returns_raw_quotes(monkeypatch):
    monkeypatch.setattr(kalshi, "fetch_contracts", lambda v, d: [
        {"floor": None, "cap": 98, "yes_bid": 0.02, "yes_ask": 0.05},
        {"floor": 99, "cap": 100, "yes_bid": 0.93, "yes_ask": 0.97},
    ])
    assert kalshi.ask_rows("high", date(2026, 7, 19)) == [
        [None, 98, 0.02, 0.05], [99, 100, 0.93, 0.97]]


def _snap():
    return {"today": {"day": "2026-07-20"}, "tomorrow": {"day": "2026-07-21"}}


def test_yesterday_market_attached_in_the_final_hour(monkeypatch):
    monkeypatch.setattr(scheduled_log.kalshi, "implied_block",
                        lambda t, m: {"today": {}, "tomorrow": {}})
    monkeypatch.setattr(scheduled_log.kalshi, "implied_forecast",
                        lambda v, d: {"ev": 98.9, "buckets": [[99, 100, 1.0]]})
    monkeypatch.setattr(scheduled_log.kalshi, "ask_rows", lambda v, d: [[99, 100, 0.9, 0.95]])

    snap = _snap()
    scheduled_log._attach_market(snap, datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    assert snap["market"]["yesterday"]["high"]["ev"] == 98.9


def test_no_yesterday_market_during_the_day(monkeypatch):
    monkeypatch.setattr(scheduled_log.kalshi, "implied_block",
                        lambda t, m: {"today": {}, "tomorrow": {}})
    monkeypatch.setattr(scheduled_log.kalshi, "implied_forecast",
                        lambda v, d: {"ev": 1.0, "buckets": []})
    snap = _snap()
    scheduled_log._attach_market(snap, datetime(2026, 7, 20, 15, 0, tzinfo=_TZ))
    assert "yesterday" not in snap["market"]


def test_asks_attached_only_on_close_slots(monkeypatch):
    monkeypatch.setattr(scheduled_log.kalshi, "implied_block",
                        lambda t, m: {"today": {}, "tomorrow": {}})
    monkeypatch.setattr(scheduled_log.kalshi, "implied_forecast",
                        lambda v, d: {"ev": 98.9, "buckets": []})
    monkeypatch.setattr(scheduled_log.kalshi, "ask_rows", lambda v, d: [[99, 100, 0.9, 0.95]])

    at_close = _snap()
    scheduled_log._attach_market(at_close, datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    assert at_close["market_asks"]["high"] == [[99, 100, 0.9, 0.95]]

    midday = _snap()
    scheduled_log._attach_market(midday, datetime(2026, 7, 20, 15, 0, tzinfo=_TZ))
    assert "market_asks" not in midday


def test_market_failure_never_breaks_the_snapshot(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kalshi down")

    monkeypatch.setattr(scheduled_log.kalshi, "implied_block", boom)
    monkeypatch.setattr(scheduled_log.kalshi, "implied_forecast", boom)
    monkeypatch.setattr(scheduled_log.kalshi, "ask_rows", boom)
    snap = _snap()
    scheduled_log._attach_market(snap, datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    assert snap["today"]["day"] == "2026-07-20"     # untouched, no raise
