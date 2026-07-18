"""forecast_log.record stamps candidate_consensus when the snapshot carries it."""
from datetime import datetime
from zoneinfo import ZoneInfo

import forecast_log
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _pred(day_iso, hi, lo):
    return {
        "day": day_iso,
        "high": {"consensus": hi, "probabilities": {"95": 1.0}},
        "low": {"consensus": lo, "probabilities": {"78": 1.0}},
    }


def _snap(candidate=None):
    now = datetime(2026, 7, 18, 12, 0, tzinfo=_TZ)
    snap = {
        "updated": now.isoformat(timespec="seconds"),
        "today": _pred("2026-07-18", 95, 78),
        "tomorrow": _pred("2026-07-19", 96, 79),
    }
    if candidate is not None:
        snap["candidate"] = candidate
    return snap


def test_candidate_consensus_recorded_when_present(tmp_path):
    path = str(tmp_path / "log.jsonl")
    cand = {"today": _pred("2026-07-18", 96.2, 77.5),
            "tomorrow": _pred("2026-07-19", 95.0, 79.0)}
    forecast_log.record(_snap(cand), path=path)
    rows = forecast_log.load(path)
    today_high = next(r for r in rows if r["target_date"] == "2026-07-18"
                      and r["variable"] == "high" and "capture_cohort" not in r)
    assert today_high["candidate_consensus"] == 96.2


def test_no_candidate_key_when_absent(tmp_path):
    path = str(tmp_path / "log.jsonl")
    forecast_log.record(_snap(), path=path)
    rows = forecast_log.load(path)
    assert all("candidate_consensus" not in r for r in rows)
