"""The Models column: the consensus shown beside Ref on a candidate row."""
from datetime import datetime, timedelta, timezone

import screen_view

_NOW = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)

_DOC = {
    "generated": "2026-08-07T19:45:00Z",
    "cities": {"DEN": {"name": "Denver", "timezone": "America/Denver", "days": {
        "2026-08-07": {
            "high": {"nws": 95.0, "nws_folded": 96.0, "cons": 92.1,
                     "cons_folded": 96.0, "spread": 1.6, "n": 5, "models": {}},
            "low": {"nws": 63.0, "nws_folded": 61.0, "cons": 63.4,
                    "cons_folded": 61.0, "spread": 0.8, "n": 4, "models": {}},
        }}}},
}


def _row(ticker="KXLOWTDEN-26AUG07-B62.5", series="KXLOWTDEN", variable="low"):
    return {"series": series, "ticker": ticker, "variable": variable}


def test_the_cell_shows_the_folded_consensus_and_its_spread():
    # Folded, because Ref one cell away is folded -- an unfolded number beside
    # it would invite a false comparison.
    assert screen_view.consensus_cell(_row(), _DOC, _NOW) == "61.0 ±0.8"


def test_a_high_reads_its_own_variable():
    row = _row("KXHIGHDEN-26AUG07-T94", "KXHIGHDEN", "high")
    assert screen_view.consensus_cell(row, _DOC, _NOW) == "96.0 ±1.6"


def test_a_city_absent_from_the_document_reads_as_a_dash():
    row = _row("KXLOWTMIA-26AUG07-B76.5", "KXLOWTMIA", "low")
    assert screen_view.consensus_cell(row, _DOC, _NOW) == "—"


def test_a_day_the_document_does_not_cover_reads_as_a_dash():
    assert screen_view.consensus_cell(
        _row("KXLOWTDEN-26AUG12-B62.5"), _DOC, _NOW) == "—"


def test_a_stale_document_shows_nothing_rather_than_something_wrong():
    old = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)     # 10+ hours later
    assert screen_view.consensus_cell(_row(), _DOC, old) == "—"
    assert screen_view.doc_is_fresh(_DOC, old) is False


def test_a_document_within_the_window_is_fresh():
    assert screen_view.doc_is_fresh(_DOC, _NOW) is True


def test_an_unreadable_document_never_raises():
    assert screen_view.consensus_cell(_row(), {}, _NOW) == "—"
    assert screen_view.consensus_cell(_row(), None, _NOW) == "—"


def test_the_column_sits_next_to_the_reference_it_qualifies():
    cols = screen_view._COLUMNS
    assert cols[cols.index("Ref") + 1] == "Models"


def test_the_column_is_explained():
    tip = screen_view._TIPS["Models"]
    assert "spread" in tip.lower()
