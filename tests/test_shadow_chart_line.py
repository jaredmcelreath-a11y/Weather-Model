"""The shadow (candidate) consensus is logged intraday and drawn as a 4th line."""
from datetime import datetime, timedelta

import consensus_log
from market_view import consensus_history_df


def _snap(when, cand_high=None):
    snap = {
        "updated": when.isoformat(timespec="seconds"),
        "current": {"temp": 88.0},
        "today": {"day": when.date().isoformat(),
                  "high": {"consensus": 96.0}, "low": {"consensus": 80.0}},
    }
    if cand_high is not None:
        snap["candidate"] = {
            "today": {"day": when.date().isoformat(),
                      "high": {"consensus": cand_high},
                      "low": {"consensus": 80.5}},
        }
    return snap


def test_record_captures_candidate_consensus(tmp_path):
    path = str(tmp_path / "c.jsonl")
    consensus_log.record(_snap(datetime(2026, 7, 18, 12, 0), cand_high=96.4), path=path)
    rows = consensus_log.load(path)
    hi = next(r for r in rows if r["variable"] == "high")
    assert hi["candidate_consensus"] == 96.4


def test_record_omits_candidate_when_absent(tmp_path):
    path = str(tmp_path / "c.jsonl")
    consensus_log.record(_snap(datetime(2026, 7, 18, 12, 0)), path=path)
    rows = consensus_log.load(path)
    assert all("candidate_consensus" not in r for r in rows)


def test_history_df_has_shadow_column():
    day = "2026-07-18"
    rows = [
        {"target_date": day, "variable": "high", "basis": "cli",
         "captured_at": f"{day}T12:00:00", "consensus": 96.0,
         "candidate_consensus": 96.4},
        {"target_date": day, "variable": "high", "basis": "cli",
         "captured_at": f"{day}T12:10:00", "consensus": 96.2,
         "candidate_consensus": 96.5},
    ]
    df = consensus_history_df(rows, day, "high", "cli", include_temp=True)
    assert "shadow" in df.columns
    assert list(df["shadow"]) == [96.4, 96.5]
