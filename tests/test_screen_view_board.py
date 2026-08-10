"""The 20-city board: a number for every city, flagged or not."""
from datetime import datetime, timezone

import screen_view

_NOW = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)

_DOC = {
    "generated": "2026-08-07T19:45:00Z",
    "cities": {
        "DEN": {"name": "Denver", "timezone": "America/Denver", "days": {
            "2026-08-07": {
                "high": {"nws_folded": 95.0, "cons_folded": 92.1,
                         "spread": 1.4, "n": 5},
                "low": {"nws_folded": 63.0, "cons_folded": 63.4,
                        "spread": 0.8, "n": 5}},
            "2026-08-08": {
                "high": {"nws_folded": 97.0, "cons_folded": 98.2,
                         "spread": 2.0, "n": 4},
                "low": {"nws_folded": 64.0, "cons_folded": None,
                        "spread": None, "n": 0}}}},
        "MIA": {"name": "Miami", "timezone": "America/New_York", "days": {
            "2026-08-07": {
                "high": {"nws_folded": 91.0, "cons_folded": 91.2,
                         "spread": 0.6, "n": 5},
                "low": {"nws_folded": 79.0, "cons_folded": 78.6,
                        "spread": 1.1, "n": 5}}}},
    },
}


def test_the_board_lists_every_city_alphabetically():
    rows = screen_view.board_rows(_DOC, "Today", _NOW)
    assert [r["City"] for r in rows] == ["Denver", "Miami"]


def test_a_row_pairs_each_forecast_with_the_models_and_their_gap():
    rows = screen_view.board_rows(_DOC, "Today", _NOW)
    denver = rows[0]
    assert denver["Hi NWS"] == "95.0"
    assert denver["Hi Models"] == "92.1 ±1.4"
    assert denver["Hi Δ"] == "−2.9"
    assert denver["Lo Δ"] == "+0.4"


def test_the_delta_uses_the_apps_true_minus_sign():
    # Every other negative on this page uses U+2212, not a hyphen.
    rows = screen_view.board_rows(_DOC, "Today", _NOW)
    assert "−" in rows[0]["Hi Δ"] and "-" not in rows[0]["Hi Δ"]


def test_tomorrow_is_a_different_day_not_a_different_table():
    rows = screen_view.board_rows(_DOC, "Tomorrow", _NOW)
    denver = [r for r in rows if r["City"] == "Denver"][0]
    assert denver["Hi Models"] == "98.2 ±2.0"


def test_a_city_without_that_day_is_omitted_rather_than_blank():
    # Miami has no Aug 8 block here; a row of dashes says nothing.
    rows = screen_view.board_rows(_DOC, "Tomorrow", _NOW)
    assert [r["City"] for r in rows] == ["Denver"]


def test_a_variable_with_no_consensus_dashes_only_its_own_cells():
    rows = screen_view.board_rows(_DOC, "Tomorrow", _NOW)
    denver = rows[0]
    assert denver["Lo Models"] == "—" and denver["Lo Δ"] == "—"
    assert denver["Hi Models"] == "98.2 ±2.0"       # the high still reports


def test_a_stale_document_yields_no_board():
    old = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)
    assert screen_view.board_rows(_DOC, "Today", old) == []


def test_an_empty_document_yields_no_board():
    assert screen_view.board_rows({}, "Today", _NOW) == []


def test_every_board_column_has_a_cell():
    rows = screen_view.board_rows(_DOC, "Today", _NOW)
    for column in screen_view._BOARD_COLUMNS:
        assert column in rows[0]


def test_the_board_columns_are_explained():
    untipped = [c for c in screen_view._BOARD_COLUMNS
                if c not in screen_view._BOARD_TIPS]
    assert untipped == ["City"]


def test_the_two_delta_columns_have_distinct_names():
    # _table keys cells by column name; two columns both called 'Δ' would
    # render the high's gap in the low's cell.
    assert len(set(screen_view._BOARD_COLUMNS)) == len(screen_view._BOARD_COLUMNS)
