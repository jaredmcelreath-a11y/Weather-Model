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
         "yes_bid": 0.95, "yes_ask": 0.96, "no_ask": 0.06}
    snap = {"probabilities": {"98": 0.5, "99": 0.5}}
    out = tl.size_bracket(c, snap, {"yes": [], "no": []}, bankroll=10.0,
                          params={"max_price": 0.94, "min_price": 0.10,
                                  "kelly_fraction": 0.25, "per_market_cap": 0.50})
    assert out["contracts"] == 0
    assert "price" in out["note"].lower()
    # The note names the side, so "0.96" can't be misread as a NO quote.
    assert "yes" in out["note"].lower()


# --- require_edge: no-edge shadow entry -------------------------------------

# A bracket the model prices at p=0.60 with no YES edge (0.60 - 0.65 < 0). Its
# 0.65 ask comes from a resting NO bid at 0.35 (yes_ask = 1 - no_bid).
_NO_EDGE_CONTRACT = {"ticker": "B", "strike_type": "between", "floor": 90, "cap": 90,
                     "yes_bid": 0.63, "yes_ask": 0.65, "no_ask": 0.42}
_NO_EDGE_SNAP = {"probabilities": {"90": 0.60}}
_NO_EDGE_BOOK = {"yes": [], "no": [[0.35, 100]]}   # ask_ladder(yes) -> [(0.65, 100)]
_BASE_PARAMS = {"max_price": 0.94, "min_price": 0.10, "kelly_fraction": 0.25,
                "per_market_cap": 0.50}


def test_size_bracket_no_edge_skips_when_require_edge_default():
    # Default (key absent -> require_edge True): the edge gate still blocks.
    out = tl.size_bracket(_NO_EDGE_CONTRACT, _NO_EDGE_SNAP, _NO_EDGE_BOOK,
                          bankroll=10.0, params=_BASE_PARAMS)
    assert out["contracts"] == 0
    assert "edge" in out["note"].lower()


def test_size_bracket_no_edge_trades_one_when_require_edge_off():
    # require_edge False: a no-edge bracket still buys a single shadow contract
    # on YES, within price bounds and the cap.
    out = tl.size_bracket(_NO_EDGE_CONTRACT, _NO_EDGE_SNAP, _NO_EDGE_BOOK,
                          bankroll=10.0,
                          params={**_BASE_PARAMS, "per_market_cap": 1.00,
                                  "require_edge": False})
    assert out["side"] == "yes"
    assert out["contracts"] == 1
    assert out["avg_price"] is not None


def test_size_bracket_require_edge_off_is_uniform_single_contract():
    # Even with a strong edge and a deep book, relaxed mode buys exactly one
    # contract — Kelly sizing (and its fraction) no longer applies, so the params
    # need not even carry a kelly_fraction.
    c = {"ticker": "D", "strike_type": "between", "floor": 90, "cap": 90,
         "yes_ask": 0.30, "no_ask": 0.75}
    snap = {"probabilities": {"90": 0.60}}         # yes edge +0.30, deep book
    book = {"yes": [], "no": [[0.70, 100]]}        # ask_ladder(yes) -> [(0.30, 100)]
    out = tl.size_bracket(c, snap, book, bankroll=10.0,
                          params={"max_price": 0.94, "min_price": 0.10,
                                  "per_market_cap": 1.00, "require_edge": False})
    assert out["side"] == "yes"
    assert out["contracts"] == 1


def test_size_bracket_require_edge_off_still_respects_per_market_cap():
    # A single contract priced above the $0.50 cap is still blocked even with the
    # edge requirement off — removing the edge gate is not removing the cap.
    dear = {"ticker": "C", "strike_type": "between", "floor": 90, "cap": 90,
            "yes_bid": 0.58, "yes_ask": 0.60, "no_ask": 0.42}
    book = {"yes": [], "no": [[0.40, 100]]}          # ask_ladder(yes) -> [(0.60, 100)]
    out = tl.size_bracket(dear, _NO_EDGE_SNAP, book, bankroll=10.0,
                          params={**_BASE_PARAMS, "require_edge": False})
    assert out["contracts"] == 0


# --- YES-only: never bet against the trader's own target bracket -------------

# The live 2026-07-28 KAUS low: the market has already resolved the winning
# bracket (98c bid, no YES offer left). The model's 4% residual tail made the NO
# side at 2c look like a positive edge, so the trader kept picking NO on the very
# bracket it had just identified as the winner. Only min_price blocked the fill;
# on 2026-07-27 the same pick (KXLOWTDAL-B79.5, NO at 1c) would have lost — the
# CLI low settled 80, inside the bracket.
_SETTLED_CONTRACT = {"ticker": "KXLOWTAUS-26JUL28-B75.5", "strike_type": "between",
                     "floor": 75, "cap": 76, "yes_bid": 0.98, "yes_ask": 1.0,
                     "no_ask": 0.02}
_SETTLED_SNAP = {"probabilities": {"75": 0.32, "76": 0.64}}   # p(bracket) = 0.96


def test_size_bracket_never_buys_no_on_its_own_target():
    for require_edge in (True, False):
        out = tl.size_bracket(_SETTLED_CONTRACT, _SETTLED_SNAP, {"yes": [], "no": []},
                              bankroll=10.0,
                              params={**_BASE_PARAMS, "require_edge": require_edge})
        assert out["side"] != "no"
        assert out["contracts"] == 0


def test_size_bracket_reports_a_settled_market_as_settled():
    # Distinct from "price outside bounds": the answer is known and priced, which
    # is why there is nothing to buy — say so in the log.
    out = tl.size_bracket(_SETTLED_CONTRACT, _SETTLED_SNAP, {"yes": [], "no": []},
                          bankroll=10.0, params=_BASE_PARAMS)
    assert "settled" in out["note"].lower()


def test_market_settled_detects_resolved_and_live_brackets():
    assert tl.market_settled({"yes_bid": 0.98, "yes_ask": 1.0}) is True
    assert tl.market_settled({"yes_bid": 0.97, "yes_ask": 0.99}) is True
    assert tl.market_settled({"yes_bid": 0.60, "yes_ask": 0.65}) is False
    # No quotes at all is not "settled" — it is unquoted.
    assert tl.market_settled({"yes_bid": None, "yes_ask": None}) is False


def test_size_bracket_reports_a_missing_yes_offer():
    c = {"ticker": "U", "strike_type": "between", "floor": 90, "cap": 90,
         "yes_bid": 0.40, "yes_ask": None, "no_ask": 0.05}
    out = tl.size_bracket(c, _NO_EDGE_SNAP, {"yes": [], "no": []}, bankroll=10.0,
                          params=_BASE_PARAMS)
    assert out["contracts"] == 0
    assert "no yes offer" in out["note"].lower()


def test_size_bracket_ignores_a_juicy_no_edge():
    # Today's KAUS high shape: the model gives the market's favourite bracket
    # p=0.01, so NO at 0.24 carries a huge nominal edge and sits inside the price
    # bounds. It is still not ours to take — the target bracket IS the entry
    # thesis, and should_exit's reversal check keys off the target ticker, so a NO
    # position there would never read as reversed.
    c = {"ticker": "H", "strike_type": "between", "floor": 99, "cap": 100,
         "yes_bid": 0.76, "yes_ask": 0.77, "no_ask": 0.24}
    snap = {"probabilities": {"97": 0.53, "98": 0.17, "99": 0.01, "100": 0.0}}
    out = tl.size_bracket(c, snap, {"yes": [], "no": [[0.23, 50]]}, bankroll=10.0,
                          params=_BASE_PARAMS)          # require_edge default True
    assert out["side"] != "no"
    assert out["contracts"] == 0
    assert "edge" in out["note"].lower()
