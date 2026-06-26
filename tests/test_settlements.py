"""Tests for the persistent settlements log: actual KDFW daily highs/lows.

All synthetic — forecast_log.load and the IEM fetch helpers are monkeypatched —
so the persistence/dedup logic is exercised without any network.
"""

from datetime import date

import forecast_log
import settlements
from sources import station_history

TODAY = date(2026, 6, 18)


def _forecast_rows():
    # Model forecast for 6/16 and 6/17 (both settle before TODAY) plus 6/18
    # (TODAY itself, not yet settled).
    return [
        {"target_date": "2026-06-16", "variable": "high"},
        {"target_date": "2026-06-16", "variable": "low"},
        {"target_date": "2026-06-17", "variable": "high"},
        {"target_date": "2026-06-18", "variable": "high"},
    ]


def test_record_persists_settled_actuals(tmp_path, monkeypatch):
    p = str(tmp_path / "settlements.jsonl")
    monkeypatch.setattr(forecast_log, "load", lambda path=None: _forecast_rows())
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {date(2026, 6, 16): (90.0, 77.0),
                                      date(2026, 6, 17): (92.0, 78.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {date(2026, 6, 16): (91.0, 76.0),
                                      date(2026, 6, 17): (93.0, 77.0)})
    settlements.record(today=TODAY, path=p)
    by = {(r["target_date"], r["basis"]): r for r in settlements.load(p)}
    # both bases recorded for both settled days
    assert by[("2026-06-16", "hourly")]["high"] == 90.0
    assert by[("2026-06-16", "hourly")]["low"] == 77.0
    assert by[("2026-06-16", "cli")]["high"] == 91.0
    assert by[("2026-06-17", "cli")]["low"] == 77.0
    # TODAY (6/18) has not settled yet -> not recorded
    assert ("2026-06-18", "hourly") not in by
    assert len(by) == 4  # 2 settled days x 2 bases


def test_record_is_append_once(tmp_path, monkeypatch):
    p = str(tmp_path / "settlements.jsonl")
    monkeypatch.setattr(forecast_log, "load", lambda path=None: _forecast_rows())
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {date(2026, 6, 16): (90.0, 77.0),
                                      date(2026, 6, 17): (92.0, 78.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli", lambda s, e: {})
    settlements.record(today=TODAY, path=p)
    settlements.record(today=TODAY, path=p)  # rerun must not duplicate
    rows = settlements.load(p)
    assert len(rows) == 2  # 2 hourly days, recorded once each


def test_record_skips_days_without_actual(tmp_path, monkeypatch):
    p = str(tmp_path / "settlements.jsonl")
    monkeypatch.setattr(forecast_log, "load", lambda path=None: _forecast_rows())
    # 6/17 missing from the fetch (e.g. archive not yet posted) -> skipped, no error.
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {date(2026, 6, 16): (90.0, 77.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli", lambda s, e: {})
    settlements.record(today=TODAY, path=p)
    keys = {(r["target_date"], r["basis"]) for r in settlements.load(p)}
    assert keys == {("2026-06-16", "hourly")}


def test_as_map_returns_date_keyed_extremes(tmp_path, monkeypatch):
    p = str(tmp_path / "settlements.jsonl")
    monkeypatch.setattr(forecast_log, "load", lambda path=None: _forecast_rows())
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {date(2026, 6, 16): (90.0, 77.0)})
    monkeypatch.setattr(station_history, "fetch_actual_cli",
                        lambda s, e: {date(2026, 6, 16): (91.0, 76.0)})
    settlements.record(today=TODAY, path=p)
    assert settlements.as_map("hourly", p) == {date(2026, 6, 16): (90.0, 77.0)}
    assert settlements.as_map("cli", p) == {date(2026, 6, 16): (91.0, 76.0)}


def test_load_missing_file_is_empty(tmp_path):
    assert settlements.load(str(tmp_path / "none.jsonl")) == []
