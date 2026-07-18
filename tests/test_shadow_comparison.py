"""shadow.consensus_comparison diffs production vs candidate consensus."""
import shadow


def _pred(hi, lo):
    return {"high": {"consensus": hi}, "low": {"consensus": lo}}


def test_no_candidate_block_returns_empty():
    snap = {"today": _pred(95, 78), "tomorrow": _pred(96, 79)}
    assert shadow.consensus_comparison(snap) == []


def test_comparison_rows_and_gap():
    snap = {
        "today": _pred(95.0, 78.0), "tomorrow": _pred(96.0, 79.0),
        "candidate": {"today": _pred(96.2, 77.5), "tomorrow": _pred(95.0, 79.0)},
    }
    rows = shadow.consensus_comparison(snap)
    assert len(rows) == 4
    today_high = next(r for r in rows if r["day"] == "today" and r["variable"] == "high")
    assert today_high["production"] == 95.0
    assert today_high["candidate"] == 96.2
    assert today_high["gap"] == 1.2
    tomorrow_low = next(r for r in rows if r["day"] == "tomorrow" and r["variable"] == "low")
    assert tomorrow_low["gap"] == 0.0


def test_missing_consensus_is_none_safe():
    snap = {
        "today": _pred(None, 78.0), "tomorrow": _pred(96.0, 79.0),
        "candidate": {"today": _pred(96.0, None), "tomorrow": _pred(95.0, 79.0)},
    }
    rows = shadow.consensus_comparison(snap)
    today_high = next(r for r in rows if r["day"] == "today" and r["variable"] == "high")
    assert today_high["production"] is None
    assert today_high["gap"] is None
