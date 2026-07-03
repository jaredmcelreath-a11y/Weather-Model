from datetime import datetime
from zoneinfo import ZoneInfo
import os

import betting_log
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _at(h, m):
    return datetime(2026, 7, 3, h, m, tzinfo=_TZ)


def test_current_slot_exact_match():
    assert betting_log.current_slot(_at(15, 30)) == "15:30"


def test_current_slot_within_tolerance():
    assert betting_log.current_slot(_at(15, 4)) == "15:00"    # +4 min
    assert betting_log.current_slot(_at(16, 24)) == "16:30"   # -6 min


def test_current_slot_outside_tolerance_is_none():
    assert betting_log.current_slot(_at(15, 12)) is None      # 12 min off any slot


def test_current_slot_all_five_slots_defined():
    assert betting_log.SLOTS == ["15:00", "15:30", "16:00", "16:30", "17:00"]


_CLI = {
    "today": {
        "day": "2026-07-03",
        "high": {"consensus": 97.9, "probabilities": {"97": 0.4, "98": 0.35, "96": 0.15, "99": 0.1},
                 "observed_so_far": 91.94, "observed_continuous": 93.2,
                 "peak_locked": False, "sigma_used": 1.1},
        "low": {"consensus": 78.0, "probabilities": {"78": 0.5, "77": 0.3, "79": 0.2},
                "observed_so_far": 79.0, "observed_continuous": 79.0,
                "peak_locked": True, "sigma_used": 0.8},
    },
    "market": {"today": {
        "high": {"ev": 96.9, "buckets": [[None, 96, 0.3], [97, 98, 0.6], [99, 100, 0.1]], "volume": 5000.0},
        "low": {"ev": 78.1, "buckets": [[77, 78, 0.7], [79, 80, 0.3]], "volume": 500.0},
    }},
}
_HOURLY = {"today": {"day": "2026-07-03",
                     "high": {"consensus": 97.0}, "low": {"consensus": 78.0}}}
_CALIB = {"settlement_offset": {"high": 0.89, "high_std": 0.77, "low": -0.33, "low_std": 0.47}}


def test_record_writes_today_high_and_low(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)
    rows = betting_log.load(p)
    assert {r["variable"] for r in rows} == {"high", "low"}
    hi = next(r for r in rows if r["variable"] == "high")
    assert hi["capture_slot"] == "15:30"
    assert hi["target_date"] == "2026-07-03"
    assert hi["cli_consensus"] == 97.9
    assert hi["hourly_consensus"] == 97.0
    assert hi["flat_offset"] == 0.89
    assert round(hi["live_gap"], 2) == 1.26        # 93.2 - 91.94
    assert hi["peak_locked"] is False
    assert hi["market_ev"] == 96.9
    assert hi["model_bins"][0] == ["97", 0.4]      # top model bin
    assert hi["market_buckets"][1] == [97, 98, 0.6]


def test_record_upserts_same_slot(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)   # same slot again
    rows = [r for r in betting_log.load(p) if r["variable"] == "high"]
    assert len(rows) == 1                                        # overwritten, not appended


def test_record_distinct_slots_both_persist(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "15:00", _CALIB, path=p)
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)
    slots = sorted(r["capture_slot"] for r in betting_log.load(p) if r["variable"] == "high")
    assert slots == ["15:00", "15:30"]


def test_record_market_absent_is_omitted(tmp_path):
    p = str(tmp_path / "b.jsonl")
    cli_no_market = {"today": _CLI["today"]}                     # no "market" key
    betting_log.record(cli_no_market, _HOURLY, "16:00", _CALIB, path=p)
    hi = next(r for r in betting_log.load(p) if r["variable"] == "high")
    assert "market_ev" not in hi and "market_buckets" not in hi
