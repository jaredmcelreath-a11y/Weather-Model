import trade_logic as tl


def test_stop_loss_trigger_on_ask():
    # entry ask 0.60, stop 0.20 -> exit when ask <= 0.40
    assert tl.stop_loss_hit(0.60, 0.40, 0.20) is True
    assert tl.stop_loss_hit(0.60, 0.41, 0.20) is False


def test_stop_loss_ignores_spread_at_entry():
    # ask still 0.60 right after fill -> not underwater despite a low bid
    assert tl.stop_loss_hit(0.60, 0.60, 0.20) is False


def test_should_exit_on_stop_loss():
    pos = {"ticker": "A", "side": "yes", "count": 1, "entry_ask": 0.60}
    out, reason = tl.should_exit(pos, current_ask=0.39, target_ticker="A",
                                 gates_ok=True, params={"stop_loss": 0.20})
    assert out is True and "stop" in reason.lower()


def test_should_exit_on_reversal_new_target():
    pos = {"ticker": "A", "side": "yes", "count": 1, "entry_ask": 0.60}
    out, reason = tl.should_exit(pos, current_ask=0.60, target_ticker="B",
                                 gates_ok=True, params={"stop_loss": 0.20})
    assert out is True and "revers" in reason.lower()


def test_should_exit_on_gate_fire():
    pos = {"ticker": "A", "side": "yes", "count": 1, "entry_ask": 0.60}
    out, reason = tl.should_exit(pos, current_ask=0.60, target_ticker="A",
                                 gates_ok=False, params={"stop_loss": 0.20})
    assert out is True and "gate" in reason.lower()


def test_hold_when_nothing_triggers():
    pos = {"ticker": "A", "side": "yes", "count": 1, "entry_ask": 0.60}
    out, _ = tl.should_exit(pos, current_ask=0.60, target_ticker="A",
                            gates_ok=True, params={"stop_loss": 0.20})
    assert out is False


def test_reentry_only_into_different_bracket():
    assert tl.reentry_allowed("A", "B") is True
    assert tl.reentry_allowed("A", "A") is False
    assert tl.reentry_allowed(None, "A") is True


def test_size_bracket_respects_price_ceiling():
    c = {"ticker": "A", "strike_type": "between", "floor": 98, "cap": 99,
         "yes_ask": 0.96, "no_ask": 0.06}
    snap = {"probabilities": {"98": 0.5, "99": 0.5}}
    out = tl.size_bracket(c, snap, {"yes": [], "no": []}, bankroll=10.0,
                          params={"max_price": 0.94, "min_price": 0.10,
                                  "kelly_fraction": 0.25, "per_market_cap": 0.50})
    assert out["contracts"] == 0
    assert "price" in out["note"].lower()
