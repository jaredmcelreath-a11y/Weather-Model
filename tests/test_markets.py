"""Unit tests for the market adapters — verify each adapter wires the model→
contract price mapping to the right model function and carries the right
safe-hold defaults. Uses a synthetic probs dict so the math is independent of
live data."""

from datetime import date

import model
from markets import ROBINHOOD, KALSHI
from sources import kalshi

PROBS = {"<=85": 0.05, "86": 0.05, "87": 0.10, "88": 0.20, "89": 0.25,
         "90": 0.15, "91": 0.10, "92": 0.03, ">=93": 0.07}


def test_implied_forecast_distills_ev_and_pmf(monkeypatch):
    # Three buckets priced so the mass sits on 88-90; EV should land there.
    contracts = [
        {"strike_type": "less", "floor": None, "cap": 86,
         "yes_bid": 0.04, "yes_ask": 0.06, "volume": 10},
        {"strike_type": "between", "floor": 86, "cap": 88,
         "yes_bid": 0.18, "yes_ask": 0.22, "volume": 50},
        {"strike_type": "between", "floor": 88, "cap": 90,
         "yes_bid": 0.55, "yes_ask": 0.65, "volume": 80},
        {"strike_type": "greater", "floor": 90, "cap": None,
         "yes_bid": 0.10, "yes_ask": 0.14, "volume": 20},
    ]
    monkeypatch.setattr(kalshi, "fetch_contracts", lambda v, d: contracts)
    out = kalshi.implied_forecast("high", date(2026, 6, 16))
    assert out is not None
    # PMF normalizes to 1; mass concentrated on the 88-90 bucket (mid 89).
    assert abs(sum(p for *_, p in out["buckets"]) - 1.0) < 1e-6
    assert 88 <= out["ev"] <= 90
    assert out["volume"] == 160.0


def test_implied_forecast_none_when_unpriced(monkeypatch):
    monkeypatch.setattr(kalshi, "fetch_contracts",
                        lambda v, d: [{"strike_type": "between", "floor": 88,
                                       "cap": 90, "yes_bid": None, "yes_ask": None}])
    assert kalshi.implied_forecast("high", date(2026, 6, 16)) is None


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
