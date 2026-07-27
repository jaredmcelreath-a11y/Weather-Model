"""The auto-trader's resolved floor must read the current number, not hybrid."""
import trade_logic


def _params():
    return {"min_resolved": 0.7, "agreement_tol": 2.0}


def test_resolved_floor_uses_current_not_hybrid():
    # Current below the floor, hybrid high -> blocked ON resolved (uses current).
    ok, reason = trade_logic.entry_allowed(
        {"resolved": 0.10, "resolved_hybrid": 0.99, "consensus": 100.0},
        None, _params(), "high")
    assert not ok
    assert reason.startswith("resolved")

    # Current above the floor, hybrid zero -> passes the floor using current 75%
    # (it blocks later for lack of a market center, NOT on resolved).
    ok, reason = trade_logic.entry_allowed(
        {"resolved": 0.75, "resolved_hybrid": 0.0, "consensus": 100.0},
        None, _params(), "high")
    assert "resolved" not in reason
