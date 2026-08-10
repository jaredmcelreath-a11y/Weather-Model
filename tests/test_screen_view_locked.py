"""The locked table: the YES side of the Screen page."""
from datetime import datetime, timezone

import screen_view

_NOW = datetime(2026, 8, 10, 0, 18, tzinfo=timezone.utc)
_ZONES = {"KXLOWTPHX": "America/Phoenix"}


def _row(kind="guarded", price=0.39, margin=4.0):
    return {"ts": "2026-08-10T00:00:00Z", "series": "KXLOWTPHX",
            "variable": "low", "ticker": "KXLOWTPHX-26AUG09-T91",
            "label": "92° or above", "side": "YES", "price": price,
            "reference": 93.0, "margin": margin, "kind": kind,
            "storm": 20, "hours_to_close": 6.7}


def test_a_locked_row_reads_as_a_decision():
    got = screen_view._locked_row(_row(), {"KXLOWTPHX-26AUG09-T91": 0.41},
                                  _ZONES, _NOW)
    assert got["City"] == "Phoenix"
    assert got["Day"] == "Today"
    assert got["Bracket"] == "92° or above"
    assert got["Price"] == "0.39"
    assert got["YES Now"] == "41%"
    assert got["Margin"] == "4.0"
    assert got["Kind"] == "Guarded"
    assert got["Storm"] == "20%"


def test_the_hard_kind_is_named_differently_from_the_soft_one():
    got = screen_view._locked_row(_row(kind="locked"), {}, _ZONES, _NOW)
    assert got["Kind"] == "Locked"


def test_a_row_with_no_live_quote_shows_a_dash_not_a_guess():
    got = screen_view._locked_row(_row(), {}, _ZONES, _NOW)
    assert got["YES Now"] == "—"


def test_rows_the_market_now_agrees_with_are_dropped():
    # Same band as the rules, applied to the live quote.
    rows = [_row()]
    visible, dear, cheap = screen_view.yes_tradeable_now(
        rows, {"KXLOWTPHX-26AUG09-T91": 0.95})
    assert visible == [] and dear == 1


def test_rows_the_market_says_we_are_wrong_about_are_dropped():
    rows = [_row()]
    visible, dear, cheap = screen_view.yes_tradeable_now(
        rows, {"KXLOWTPHX-26AUG09-T91": 0.04})
    assert visible == [] and cheap == 1


def test_a_row_without_a_live_quote_survives():
    rows = [_row()]
    visible, dear, cheap = screen_view.yes_tradeable_now(rows, {})
    assert visible == rows


def test_every_locked_column_has_a_cell():
    got = screen_view._locked_row(_row(), {}, _ZONES, _NOW)
    for column in screen_view._LOCKED_COLUMNS:
        assert column in got


def test_the_locked_columns_are_explained():
    untipped = [c for c in screen_view._LOCKED_COLUMNS
                if c not in screen_view._LOCKED_TIPS]
    assert untipped == ["City", "Var", "Bracket"]


def test_the_locked_table_does_not_inherit_the_fade_tables_meanings():
    # Gap and Str measure distance from the reference TO the bracket, which is
    # identically zero for a row whose realized extreme is already inside it.
    assert "Gap" not in screen_view._LOCKED_COLUMNS
    assert "Str" not in screen_view._LOCKED_COLUMNS


def test_live_yes_prices_fetches_one_ladder_per_series():
    calls = []

    def fetch(series):
        calls.append(series)
        return [{"ticker": "KXLOWTPHX-26AUG09-T91", "yes_ask_dollars": "0.3900"}]

    got = screen_view.live_yes_prices([_row(), _row()], fetch=fetch)
    assert calls == ["KXLOWTPHX"]
    assert got["KXLOWTPHX-26AUG09-T91"] == 0.39


def test_live_yes_prices_survives_a_dead_series():
    def fetch(series):
        raise RuntimeError("Kalshi down")

    assert screen_view.live_yes_prices([_row()], fetch=fetch) == {}
