"""The leading bracket on a ladder, and when a ladder counts as unsettled."""
from __future__ import annotations

import screen
import screen_rules


def _row(ticker, label, ask, bid=None):
    return {"ticker": ticker, "label": label, "yes_ask": ask, "yes_bid": bid}


def test_the_leader_is_the_highest_asked_bracket_with_its_runner_up():
    rows = [_row("A", "88° to 89°", 0.12),
            _row("B", "90° to 91°", 0.71),
            _row("C", "92° to 93°", 0.24)]
    got = screen_rules.leading_bracket(rows)
    assert got["ticker"] == "B"
    assert got["label"] == "90° to 91°"
    assert got["price"] == 0.71
    assert got["next_label"] == "92° to 93°"
    assert got["next_price"] == 0.24


def test_a_lone_bracket_has_no_runner_up_rather_than_a_zero():
    # A zero would read as "the market prices the alternative at nothing",
    # which is a claim the single-row ladder never made.
    got = screen_rules.leading_bracket([_row("A", "90° to 91°", 0.71)])
    assert got["next_label"] is None
    assert got["next_price"] is None


def test_an_unquoted_ladder_has_no_leader():
    assert screen_rules.leading_bracket([]) is None
    assert screen_rules.leading_bracket([_row("A", "90° to 91°", None)]) is None


def test_a_bracket_quoted_only_on_the_bid_still_leads():
    # price_of falls back to the bid when there is no offer: you cannot trade
    # the midpoint, but an absent ASK is thin liquidity, not an absent market.
    got = screen_rules.leading_bracket([_row("A", "90° to 91°", None, 0.64)])
    assert got["price"] == 0.64


def test_two_brackets_at_the_same_price_order_deterministically():
    # A tie that reordered between firings would make the published leader
    # flicker between two identical-looking documents.
    rows = [_row("Z", "90° to 91°", 0.40), _row("A", "92° to 93°", 0.40)]
    assert screen_rules.leading_bracket(rows)["ticker"] == "A"
    assert screen_rules.leading_bracket(rows[::-1])["ticker"] == "A"


def test_the_threshold_is_ninety_cents_exclusive():
    # At 90c the market has picked its answer. The boundary matters: 0.90 is
    # settled, 0.89 is not.
    assert screen_rules.is_unsettled({"price": 0.89})
    assert not screen_rules.is_unsettled({"price": 0.90})
    assert not screen_rules.is_unsettled({"price": 0.91})
    assert not screen_rules.is_unsettled({"price": None})
    assert not screen_rules.is_unsettled(None)


def test_merge_reference_drops_a_carried_city_leader():
    # A stale leader price is exactly the failure mode merge_reference exists
    # to prevent, so it is dropped like `realized` and `remaining`.
    previous = {"cities": {"KXHIGHCHI": {
        "station": "KMDW", "timezone": "America/Chicago",
        "days": {"2026-08-16": 90.0},
        "leader": {"2026-08-16": {"ticker": "T", "price": 0.55}}}}}
    got = screen.merge_reference(previous, {"cities": {}})
    assert got["KXHIGHCHI"]["station"] == "KMDW"
    assert "leader" not in got["KXHIGHCHI"]
