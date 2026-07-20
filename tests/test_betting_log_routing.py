from datetime import datetime
from zoneinfo import ZoneInfo

import betting_log
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)

_CALIB = {"settlement_offset": {"high": 0.89, "high_std": 0.77,
                                "low": -0.33, "low_std": 0.47}}


def _var(consensus, top):
    return {"consensus": consensus, "probabilities": {top: 1.0},
            "observed_so_far": consensus, "observed_continuous": consensus,
            "peak_locked": False, "sigma_used": 1.0}


_SNAP = {
    "updated": "2026-07-20T00:45:00-05:00",
    "yesterday": {"day": "2026-07-19", "high": _var(99.0, "99"), "low": _var(79.0, "79")},
    "today": {"day": "2026-07-20", "high": _var(101.0, "101"), "low": _var(80.0, "80")},
    "tomorrow": {"day": "2026-07-21", "high": _var(97.0, "97"), "low": _var(78.0, "78")},
    "market": {
        "yesterday": {"high": {"ev": 98.9, "buckets": [[99, 100, 1.0]], "volume": 40.0},
                      "low": {"ev": 79.1, "buckets": [[79, 80, 1.0]], "volume": 10.0}},
        "today": {"high": {"ev": 100.8, "buckets": [[101, 102, 1.0]], "volume": 900.0}},
        "tomorrow": {"high": {"ev": 96.8, "buckets": [[97, 98, 1.0]], "volume": 300.0},
                     "low": {"ev": 78.2, "buckets": [[77, 78, 1.0]], "volume": 80.0}},
    },
    "market_asks": {"high": [[None, 98, 0.02, 0.05], [99, 100, 0.93, 0.97]],
                    "low": [[79, 80, 0.90, 0.94]]},
}
_HOURLY = {
    "yesterday": {"day": "2026-07-19", "high": {"consensus": 98.2}, "low": {"consensus": 78.6}},
    "today": {"day": "2026-07-20", "high": {"consensus": 100.2}, "low": {"consensus": 79.6}},
    "tomorrow": {"day": "2026-07-21", "high": {"consensus": 96.2}, "low": {"consensus": 77.6}},
}


def test_close_slot_writes_the_prior_day_from_the_yesterday_block(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "close-15", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    rows = betting_log.load(p)
    assert {r["variable"] for r in rows} == {"high", "low"}
    assert {r["target_date"] for r in rows} == {"2026-07-19"}
    hi = next(r for r in rows if r["variable"] == "high")
    assert hi["cli_consensus"] == 99.0          # yesterday block, not today
    assert hi["hourly_consensus"] == 98.2
    assert hi["market_ev"] == 98.9              # market["yesterday"]


def test_close_slot_logs_raw_asks(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "close-15", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    hi = next(r for r in betting_log.load(p) if r["variable"] == "high")
    assert hi["market_asks"] == [[None, 98, 0.02, 0.05], [99, 100, 0.93, 0.97]]


def test_evening_slot_writes_tomorrow(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "eve-22:00", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 22, 0, tzinfo=_TZ))
    rows = betting_log.load(p)
    assert {r["target_date"] for r in rows} == {"2026-07-21"}
    hi = next(r for r in rows if r["variable"] == "high")
    assert hi["cli_consensus"] == 97.0
    assert hi["market_ev"] == 96.8
    assert "market_asks" not in hi          # asks are close-slot only


def test_same_day_slot_still_reads_today(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "15:30", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 15, 30, tzinfo=_TZ))
    rows = betting_log.load(p)
    assert {r["variable"] for r in rows} == {"high"}
    assert rows[0]["target_date"] == "2026-07-20"
    assert rows[0]["cli_consensus"] == 101.0
    assert "market_asks" not in rows[0]


def test_winter_close_slot_reads_the_today_block(tmp_path):
    # In CST the ending climate day IS clock-today, so there is no yesterday block.
    snap = {"updated": "2026-01-05T23:45:00-06:00",
            "today": {"day": "2026-01-05", "high": _var(55.0, "55"), "low": _var(33.0, "33")},
            "tomorrow": {"day": "2026-01-06", "high": _var(58.0, "58"), "low": _var(35.0, "35")},
            "market": {"today": {"high": {"ev": 54.9, "buckets": [[55, 56, 1.0]]}}}}
    hourly = {"today": {"day": "2026-01-05", "high": {"consensus": 54.5},
                        "low": {"consensus": 32.5}}}
    p = str(tmp_path / "b.jsonl")
    betting_log.record(snap, hourly, "close-15", _CALIB, path=p,
                       now=datetime(2026, 1, 5, 23, 45, tzinfo=_TZ))
    rows = betting_log.load(p)
    assert {r["target_date"] for r in rows} == {"2026-01-05"}
    assert next(r for r in rows if r["variable"] == "high")["cli_consensus"] == 55.0


def test_missing_target_block_writes_nothing(tmp_path):
    # A close slot with no block for the ending day must skip, not mis-file.
    snap = {"updated": "2026-07-20T00:45:00-05:00",
            "today": {"day": "2026-07-20", "high": _var(101.0, "101")}}
    p = str(tmp_path / "b.jsonl")
    betting_log.record(snap, {}, "close-15", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 0, 45, tzinfo=_TZ))
    assert betting_log.load(p) == []


def test_now_defaults_to_the_snapshot_timestamp(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "close-15", _CALIB, path=p)   # no now=
    assert {r["target_date"] for r in betting_log.load(p)} == {"2026-07-19"}


def test_production_rows_are_byte_identical(tmp_path):
    """Production invariance: a same-day slot's row must be exactly what the
    pre-slot-families code wrote — same keys, same values, same order."""
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "15:30", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 15, 30, tzinfo=_TZ))
    row = betting_log.load(p)[0]
    assert row == {
        "target_date": "2026-07-20",
        "variable": "high",
        "capture_slot": "15:30",
        "captured_at": "2026-07-20T00:45:00-05:00",
        "cli_consensus": 101.0,
        "hourly_consensus": 100.2,
        "flat_offset": 0.89,
        "live_gap": 0.0,
        "observed_so_far": 101.0,
        "observed_continuous": 101.0,
        "peak_locked": False,
        "sigma_used": 1.0,
        "convective_widened": False,
        "front_widened": False,
        "model_bins": [["101", 1.0]],
        "market_ev": 100.8,
        "market_buckets": [[101, 102, 1.0]],
        "market_volume": 900.0,
    }
    # Key ORDER too — the jsonl is diffed by eye on the data branch.
    assert list(row) == [
        "target_date", "variable", "capture_slot", "captured_at",
        "cli_consensus", "hourly_consensus", "flat_offset", "live_gap",
        "observed_so_far", "observed_continuous", "peak_locked", "sigma_used",
        "convective_widened", "front_widened", "model_bins",
        "market_ev", "market_buckets", "market_volume"]


def test_day_ahead_and_same_day_rows_coexist(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_SNAP, _HOURLY, "eve-22:00", _CALIB, path=p,
                       now=datetime(2026, 7, 20, 22, 0, tzinfo=_TZ))
    later = {**_SNAP, "today": {"day": "2026-07-21", "high": _var(97.5, "97"),
                                "low": _var(78.5, "78")}}
    betting_log.record(later, {"today": {"day": "2026-07-21", "high": {"consensus": 96.9},
                                         "low": {"consensus": 77.9}}},
                       "15:30", _CALIB, path=p,
                       now=datetime(2026, 7, 21, 15, 30, tzinfo=_TZ))
    rows = [r for r in betting_log.load(p)
            if r["target_date"] == "2026-07-21" and r["variable"] == "high"]
    assert sorted(r["capture_slot"] for r in rows) == ["15:30", "eve-22:00"]
