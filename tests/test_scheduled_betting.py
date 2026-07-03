"""The scheduled run captures a betting-log row only inside a slot window."""
from datetime import datetime
from zoneinfo import ZoneInfo

import betting_log
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def test_capture_if_slot_records_inside_window(tmp_path, monkeypatch):
    p = str(tmp_path / "b.jsonl")
    monkeypatch.setattr(betting_log, "_PATH", p)
    cli = {"today": {"day": "2026-07-03",
                     "high": {"consensus": 97.9, "probabilities": {"98": 1.0},
                              "observed_so_far": 92.0, "observed_continuous": 93.0,
                              "peak_locked": False, "sigma_used": 1.1}}}
    hourly = {"today": {"high": {"consensus": 97.0}}}
    calib = {"settlement_offset": {"high": 0.89}}
    now = datetime(2026, 7, 3, 15, 32, tzinfo=_TZ)                 # inside 15:30 ±7
    betting_log.capture_if_slot(cli, hourly, calib, now=now)
    rows = betting_log.load(p)
    assert len(rows) == 1 and rows[0]["capture_slot"] == "15:30"


def test_capture_if_slot_noop_outside_window(tmp_path, monkeypatch):
    p = str(tmp_path / "b.jsonl")
    monkeypatch.setattr(betting_log, "_PATH", p)
    cli = {"today": {"day": "2026-07-03",
                     "high": {"consensus": 97.9, "probabilities": {"98": 1.0},
                              "observed_so_far": 92.0, "observed_continuous": 93.0,
                              "peak_locked": False, "sigma_used": 1.1}}}
    now = datetime(2026, 7, 3, 12, 0, tzinfo=_TZ)                  # no slot
    betting_log.capture_if_slot(cli, {"today": {}}, {}, now=now)
    assert betting_log.load(p) == []
