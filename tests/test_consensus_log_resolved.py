"""Consensus history carries the raw three-way Resolved fields for review."""
import json
import consensus_log


def _snapshot():
    d = {"consensus": 100.0, "resolved_hybrid": 0.6, "resolved": 0.8,
         "resolved_orig": 0.5, "resolved_collapse": 0.4, "locked_ratio": 0.2,
         "low_forming": True, "probabilities": {"100": 1.0}}
    return {"updated": "2026-07-26T14:00:00-05:00", "station": "KDFW",
            "current": {"temp": 99.0},
            "today": {"day": "2026-07-26", "high": d, "low": dict(d)}}


def test_resolved_fields_and_flags_persisted(tmp_path):
    path = str(tmp_path / "consensus_history.jsonl")
    consensus_log.record(_snapshot(), path=path, basis="cli", station="KDFW")
    rows = [json.loads(line) for line in open(path)]
    high = next(r for r in rows if r["variable"] == "high")
    assert high["resolved_hybrid"] == 0.6
    assert high["resolved"] == 0.8
    assert high["resolved_orig"] == 0.5
    assert high["resolved_collapse"] == 0.4
    assert high["locked_ratio"] == 0.2
    assert high["low_forming"] is True
    # Unset flags are omitted (kept out of the row), read as falsy via .get().
    assert "convective_widened" not in high
