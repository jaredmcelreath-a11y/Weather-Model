import trade_logic as tl


def _c(floor, cap, ticker):
    return {"ticker": ticker, "strike_type": "between", "floor": floor, "cap": cap,
            "yes_ask": 0.5, "yes_bid": 0.45, "no_ask": 0.55, "no_bid": 0.5}


def test_market_center_reads_ev():
    assert tl.market_center({"ev": 99.4}) == 99.4
    assert tl.market_center(None) is None


def test_entry_blocked_below_resolved_floor():
    snap = {"resolved": 0.5, "consensus": 99.0, "low_forming": False,
            "peak_locked": True}
    ok, reason = tl.entry_allowed(snap, {"ev": 99.0},
                                  {"min_resolved": 0.70, "agreement_tol": 1.0}, "high")
    assert ok is False and "resolved" in reason.lower()


def test_entry_blocked_when_disagree():
    snap = {"resolved": 0.9, "consensus": 99.0, "low_forming": False,
            "peak_locked": True}
    ok, reason = tl.entry_allowed(snap, {"ev": 101.0},
                                  {"min_resolved": 0.70, "agreement_tol": 1.0}, "high")
    assert ok is False and "agree" in reason.lower()


def test_entry_blocked_by_low_forming_gate():
    snap = {"resolved": 0.9, "consensus": 75.0, "low_forming": True,
            "peak_locked": False}
    ok, reason = tl.entry_allowed(snap, {"ev": 75.0},
                                  {"min_resolved": 0.70, "agreement_tol": 1.0}, "low")
    assert ok is False and "forming" in reason.lower()


def test_entry_allowed_when_all_clear():
    snap = {"resolved": 0.9, "consensus": 99.0, "low_forming": False,
            "peak_locked": True, "front_widened": False, "convective_widened": False}
    ok, _ = tl.entry_allowed(snap, {"ev": 99.4},
                             {"min_resolved": 0.70, "agreement_tol": 1.0}, "high")
    assert ok is True


def test_bracket_direct_hit():
    cs = [_c(98, 99, "A"), _c(100, 101, "B")]
    got = tl.select_bracket(cs, 98.4, "high")
    assert got["ticker"] == "A"


def test_bracket_tie_high_picks_upper():
    # 99.5 sits between 98-99 and 100-101; a HIGH still climbing buys upper.
    cs = [_c(98, 99, "A"), _c(100, 101, "B")]
    got = tl.select_bracket(cs, 99.5, "high")
    assert got["ticker"] == "B"


def test_bracket_tie_low_picks_lower():
    # mirror: a forming LOW that can still fall buys the lower bracket.
    cs = [_c(98, 99, "A"), _c(100, 101, "B")]
    got = tl.select_bracket(cs, 99.5, "low")
    assert got["ticker"] == "A"
