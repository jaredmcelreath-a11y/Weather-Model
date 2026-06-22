"""Unit tests for the market adapters — verify each adapter wires the model→
contract price mapping to the right model function and carries the right
safe-hold defaults. Uses a synthetic probs dict so the math is independent of
live data."""

import model
from markets import ROBINHOOD, KALSHI

PROBS = {"<=85": 0.05, "86": 0.05, "87": 0.10, "88": 0.20, "89": 0.25,
         "90": 0.15, "91": 0.10, "92": 0.03, ">=93": 0.07}


def test_robinhood_uses_prob_for_contract():
    c = {"kind": ">", "strike": 90}
    assert ROBINHOOD.model_prob(PROBS, c) == model.prob_for_contract(PROBS, ">", 90)


def test_kalshi_between_bucket():
    c = {"strike_type": "between", "floor": 88, "cap": 89}
    assert KALSHI.model_prob(PROBS, c) == model.prob_for_strike(PROBS, "between", 88, 89)
    assert round(KALSHI.model_prob(PROBS, c), 4) == 0.45


def test_kalshi_less_bucket():
    c = {"strike_type": "less", "floor": None, "cap": 88}
    assert round(KALSHI.model_prob(PROBS, c), 4) == 0.20


def test_kalshi_greater_bucket():
    c = {"strike_type": "greater", "floor": 91, "cap": None}
    assert round(KALSHI.model_prob(PROBS, c), 4) == 0.10


def test_safe_hold_defaults():
    assert (ROBINHOOD.safe_hold_default, ROBINHOOD.safe_hold_min) == (0.80, 0.60)
    assert (KALSHI.safe_hold_default, KALSHI.safe_hold_min) == (0.55, 0.50)


def test_market_view_imports_and_exposes_render():
    import market_view
    assert callable(market_view.render_page)
    assert callable(market_view.render_variable)


def test_basis_note_kalshi_set_robinhood_none():
    assert ROBINHOOD.basis_note is None
    assert KALSHI.basis_note and "CLI" in KALSHI.basis_note


def test_accuracy_note_kalshi_set_robinhood_none():
    assert ROBINHOOD.accuracy_note is None
    assert KALSHI.accuracy_note and "CLI" in KALSHI.accuracy_note
