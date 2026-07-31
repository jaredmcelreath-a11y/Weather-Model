from datetime import date

import trade_pnl


def _entry(tkr="T1", day="2026-07-28", ask=0.60, count=1, ts="2026-07-28T15:00:00+00:00"):
    return {"ts": ts, "kind": "entry", "ticker": tkr, "side": "yes", "count": count,
            "entry_ask": ask, "variable": "high", "day": day, "floor": 98, "cap": 99}


def _exit(tkr="T1", price=1.0, pnl=0.40, ts="2026-07-29T12:00:00+00:00",
          reason="settled won (high 99)"):
    rec = {"ts": ts, "kind": "exit", "ticker": tkr, "side": "yes", "count": 1,
           "variable": "high", "reason": reason, "pnl": pnl}
    if price is not None:
        rec["exit_price"] = price
    return rec


# --- closed_trades ----------------------------------------------------------

def test_closed_trades_pairs_entry_to_exit():
    out = trade_pnl.closed_trades([_entry(), _exit()])
    assert len(out) == 1
    t = out[0]
    assert t["ticker"] == "T1" and t["day"] == date(2026, 7, 28)
    assert t["entry_ask"] == 0.60 and t["exit_price"] == 1.0
    assert t["pnl"] == 0.40


def test_closed_trades_skips_a_still_open_entry():
    assert trade_pnl.closed_trades([_entry()]) == []


def test_closed_trades_skips_a_pre_schema_exit():
    # No exit_price (logged before the schema change) -> unscorable, not zero.
    assert trade_pnl.closed_trades([_entry(), _exit(price=None, pnl=None)]) == []


# --- pre-schema settled positions, scored from the settlement map -------------

_TKR = "KXHIGHTDAL-26JUL28-B100.5"


def _bare_entry(ask=0.93, tkr=_TKR, ts="2026-07-28T21:41:36+00:00"):
    """A pre-schema entry: no variable/day/floor/cap, only the ticker."""
    return {"ts": ts, "kind": "entry", "ticker": tkr, "side": "yes",
            "count": 1, "entry_ask": ask}


def _settled_exit(tkr=_TKR, ts="2026-07-29T11:01:26+00:00"):
    return {"ts": ts, "kind": "exit", "ticker": tkr, "side": "yes", "count": 1,
            "reason": "settled (unscored, pre-schema)", "exit_price": None,
            "pnl": None}


def test_settled_pre_schema_trade_is_scored_from_the_ticker_bracket():
    # B100.5 -> the 100-101 bracket; CLI high 100 is inside -> won at $1.00.
    out = trade_pnl.closed_trades([_bare_entry(), _settled_exit()],
                                  {date(2026, 7, 28): (100.0, 80.0)})
    assert len(out) == 1
    assert out[0]["exit_price"] == 1.0
    assert out[0]["pnl"] == round(1.0 - 0.93, 4)
    assert out[0]["day"] == date(2026, 7, 28)


def test_settled_pre_schema_trade_scores_a_loss_at_zero():
    out = trade_pnl.closed_trades([_bare_entry(), _settled_exit()],
                                  {date(2026, 7, 28): (97.0, 80.0)})
    assert out[0]["exit_price"] == 0.0 and out[0]["pnl"] == round(-0.93, 4)


def test_settled_pre_schema_trade_reads_the_low_for_a_low_ticker():
    tkr = "KXLOWTDAL-26JUL28-B80.5"
    out = trade_pnl.closed_trades([_bare_entry(tkr=tkr), _settled_exit(tkr=tkr)],
                                  {date(2026, 7, 28): (100.0, 80.0)})
    assert out[0]["exit_price"] == 1.0      # low 80 inside the 80-81 bracket


def test_settled_pre_schema_no_trade_without_a_settlement():
    assert trade_pnl.closed_trades([_bare_entry(), _settled_exit()], {}) == []
    assert trade_pnl.closed_trades([_bare_entry(), _settled_exit()],
                                   {date(2026, 7, 27): (99.0, 79.0)}) == []


def test_open_ended_tail_bracket_is_never_guessed():
    # T-suffixed tails don't follow the B<mid> rule -> refuse rather than invent.
    tkr = "KXHIGHTDAL-26JUL28-T100"
    assert trade_pnl.closed_trades([_bare_entry(tkr=tkr), _settled_exit(tkr=tkr)],
                                   {date(2026, 7, 28): (100.0, 80.0)}) == []


def test_a_priced_market_exit_is_never_overridden_by_the_settlement():
    # Stop-loss sold at 0.06; the bracket settling in the money must not rewrite it.
    x = {**_settled_exit(), "exit_price": 0.06, "pnl": None,
         "reason": "stop-loss: ask 0.09 <= entry 0.29 - 0.20"}
    out = trade_pnl.closed_trades([_bare_entry(ask=0.29), x],
                                  {date(2026, 7, 28): (100.0, 80.0)})
    assert out[0]["exit_price"] == 0.06 and out[0]["pnl"] == round(0.06 - 0.29, 4)


def test_unpriced_market_exit_stays_unscored_even_with_a_settlement():
    # A reversal sold at some unrecorded bid — the settlement is NOT its exit price.
    x = {**_settled_exit(), "reason": "reversal: target moved to KXHIGHTDAL-26JUL28-B99.5"}
    assert trade_pnl.closed_trades([_bare_entry(), x],
                                   {date(2026, 7, 28): (100.0, 80.0)}) == []


# --- replayed records (runtime write lost mid-run) ----------------------------

def test_a_replayed_entry_does_not_become_a_second_trade():
    # Only one position per ticker can be open, so the repeat entry is the same
    # position re-logged; the latest ask is the one the loop stopped out against.
    recs = [_entry(ask=0.24, ts="2026-07-29T21:21:00+00:00"),
            _entry(ask=0.29, ts="2026-07-29T21:32:00+00:00"),
            _exit(price=0.06, pnl=None, ts="2026-07-29T22:11:00+00:00",
                  reason="stop-loss")]
    out = trade_pnl.closed_trades(recs)
    assert len(out) == 1
    assert out[0]["entry_ask"] == 0.29 and out[0]["pnl"] == round(0.06 - 0.29, 4)


def test_replayed_exits_are_dropped():
    recs = [_entry(ask=0.29)] + [
        _exit(price=p, pnl=None, ts=f"2026-07-29T22:{m}:00+00:00",
              reason="stop-loss")
        for p, m in ((0.06, "11"), (0.07, "17"), (0.07, "21"), (0.05, "31"))]
    out = trade_pnl.closed_trades(recs)
    assert len(out) == 1 and out[0]["exit_price"] == 0.06


def test_a_phantom_exit_before_a_settlement_is_dropped():
    """The real 2026-07-28 Austin sequence: a reversal exit was logged, the runtime
    write was lost, so the position was still held and settled the next morning.
    Both records are in the log; only the settlement actually happened."""
    tkr = "KXHIGHAUS-26JUL28-B99.5"
    recs = [
        {"ts": "2026-07-28T21:12:03+00:00", "kind": "entry", "ticker": tkr,
         "side": "yes", "count": 1, "entry_ask": 0.70},
        {"ts": "2026-07-28T21:22:09+00:00", "kind": "exit", "ticker": tkr,
         "side": "yes", "count": 1,
         "reason": "reversal: target moved to KXHIGHAUS-26JUL28-B97.5"},
        {"ts": "2026-07-29T11:01:29+00:00", "kind": "exit", "ticker": tkr,
         "side": "yes", "count": 1, "variable": "high",
         "reason": "settled (unscored, pre-schema)", "exit_price": None,
         "pnl": None},
    ]
    out = trade_pnl.closed_trades(recs, {date(2026, 7, 28): (99.0, 75.0)})
    assert len(out) == 1                      # not zero, and not two
    assert out[0]["exit_price"] == 1.0        # CLI high 99 is inside 99-100
    assert out[0]["pnl"] == round(1.0 - 0.70, 4)


def test_a_priced_exit_before_a_settlement_survives_as_its_own_trade():
    # Real stop-loss, re-entry, then held to settlement = two honest round trips.
    tkr = "KXHIGHAUS-26JUL28-B99.5"
    recs = [
        {"ts": "2026-07-28T14:00:00+00:00", "kind": "entry", "ticker": tkr,
         "side": "yes", "count": 1, "entry_ask": 0.70},
        {"ts": "2026-07-28T15:00:00+00:00", "kind": "exit", "ticker": tkr,
         "side": "yes", "count": 1, "reason": "stop-loss", "exit_price": 0.50},
        {"ts": "2026-07-28T16:00:00+00:00", "kind": "entry", "ticker": tkr,
         "side": "yes", "count": 1, "entry_ask": 0.55},
        {"ts": "2026-07-29T11:00:00+00:00", "kind": "exit", "ticker": tkr,
         "side": "yes", "count": 1, "variable": "high",
         "reason": "settled (unscored, pre-schema)", "exit_price": None,
         "pnl": None},
    ]
    out = trade_pnl.closed_trades(recs, {date(2026, 7, 28): (99.0, 75.0)})
    assert [t["pnl"] for t in out] == [round(0.50 - 0.70, 4), round(1.0 - 0.55, 4)]


def test_a_phantom_exit_on_a_different_ticker_is_left_alone():
    # The 7/28 Austin reversal OUT of B97.5 genuinely happened — B99.5 settling
    # says nothing about it, so it must not be dropped.
    recs = [
        {"ts": "2026-07-28T21:07:23+00:00", "kind": "entry",
         "ticker": "KXHIGHAUS-26JUL28-B97.5", "side": "yes", "count": 1,
         "entry_ask": 0.57},
        {"ts": "2026-07-28T21:12:02+00:00", "kind": "exit",
         "ticker": "KXHIGHAUS-26JUL28-B97.5", "side": "yes", "count": 1,
         "reason": "reversal: target moved to KXHIGHAUS-26JUL28-B99.5"},
        {"ts": "2026-07-29T11:01:29+00:00", "kind": "exit",
         "ticker": "KXHIGHAUS-26JUL28-B99.5", "side": "yes", "count": 1,
         "reason": "settled won (high 99)", "exit_price": 1.0, "pnl": 0.30},
    ]
    kept = trade_pnl._drop_lost_exits(recs)
    assert len(kept) == 3


def test_drop_lost_exits_is_a_no_op_without_any_settlement():
    recs = [{"ts": "1", "kind": "exit", "ticker": "T", "reason": "reversal"}]
    assert trade_pnl._drop_lost_exits(recs) is recs


# --- record_summary ----------------------------------------------------------

def test_record_summary_counts_wins_losses_and_pushes():
    trades = [{"pnl": 0.30}, {"pnl": -0.20}, {"pnl": 0.07}, {"pnl": 0.0}]
    s = trade_pnl.record_summary(trades)
    assert s["trades"] == 4 and s["wins"] == 2 and s["losses"] == 1
    assert s["pushes"] == 1
    assert s["win_rate"] == 2 / 3          # over decided trades only
    assert s["realized"] == 0.17


def test_record_summary_empty():
    s = trade_pnl.record_summary([])
    assert s == {"trades": 0, "wins": 0, "losses": 0, "pushes": 0,
                 "win_rate": None, "realized": 0.0}


def test_closed_trades_recomputes_a_missing_pnl():
    out = trade_pnl.closed_trades([_entry(count=2), _exit(price=1.0, pnl=None)])
    assert out[0]["pnl"] == round((1.0 - 0.60) * 2, 4)


def test_closed_trades_ignores_skips_and_halts():
    noise = [{"ts": "2026-07-28T15:00:00+00:00", "kind": "skip", "variable": "low",
              "reason": "settled"},
             {"ts": "2026-07-28T15:00:00+00:00", "kind": "halt", "reason": "daily_loss"}]
    assert len(trade_pnl.closed_trades([_entry()] + noise + [_exit()])) == 1


def test_closed_trades_handles_a_ticker_traded_twice():
    # Entered, stopped out, re-entered the same bracket later, then settled.
    recs = [_entry(ask=0.50, ts="2026-07-28T14:00:00+00:00"),
            _exit(price=0.30, pnl=-0.20, ts="2026-07-28T15:00:00+00:00",
                  reason="stop-loss"),
            _entry(ask=0.40, ts="2026-07-28T16:00:00+00:00"),
            _exit(price=1.0, pnl=0.60, ts="2026-07-29T12:00:00+00:00")]
    out = trade_pnl.closed_trades(recs)
    assert [t["pnl"] for t in out] == [-0.20, 0.60]


def test_closed_trades_falls_back_to_the_ticker_day():
    e = _entry(tkr="KXHIGHAUS-26JUL28-B99.5", day=None)
    x = _exit(tkr="KXHIGHAUS-26JUL28-B99.5")
    assert trade_pnl.closed_trades([e, x])[0]["day"] == date(2026, 7, 28)


# --- daily_pnl --------------------------------------------------------------

def test_daily_pnl_buckets_by_weather_day_not_timestamp():
    # The exit is logged on 7/29 but the trade belongs to the 7/28 weather day.
    out = trade_pnl.daily_pnl(trade_pnl.closed_trades([_entry(), _exit()]))
    assert out == [{"date": date(2026, 7, 28), "pnl": 0.40}]


def test_daily_pnl_sums_within_a_day_and_orders_oldest_first():
    recs = [_entry(tkr="A", day="2026-07-29"), _exit(tkr="A", pnl=0.10),
            _entry(tkr="B", day="2026-07-28"), _exit(tkr="B", pnl=-0.25),
            _entry(tkr="C", day="2026-07-28"), _exit(tkr="C", pnl=0.05)]
    out = trade_pnl.daily_pnl(trade_pnl.closed_trades(recs))
    assert out == [{"date": date(2026, 7, 28), "pnl": -0.20},
                   {"date": date(2026, 7, 29), "pnl": 0.10}]


# --- equity_curve -----------------------------------------------------------

def test_equity_curve_accumulates_from_zero_with_an_anchor():
    trades = trade_pnl.closed_trades([_entry(tkr="B", day="2026-07-28"),
                                      _exit(tkr="B", pnl=-0.25),
                                      _entry(tkr="A", day="2026-07-29"),
                                      _exit(tkr="A", pnl=0.10)])
    curve = trade_pnl.equity_curve(trades, date(2026, 7, 29), None)
    assert curve[0] == {"date": date(2026, 7, 27), "total": 0.0}   # anchor
    assert curve[1] == {"date": date(2026, 7, 28), "total": -0.25}
    assert curve[2] == {"date": date(2026, 7, 29), "total": -0.15}


def test_equity_curve_empty_input_is_empty():
    assert trade_pnl.equity_curve([], date(2026, 7, 29), None) == []


def test_equity_curve_appends_a_live_point_for_open_positions():
    trades = trade_pnl.closed_trades([_entry(tkr="B", day="2026-07-28"),
                                      _exit(tkr="B", pnl=-0.25)])
    # Open position marked at a 0.70 bid against a 0.50 entry -> +0.20 unrealized.
    marks = [{"ticker": "OPEN", "entry_ask": 0.50, "count": 1, "bid": 0.70}]
    curve = trade_pnl.equity_curve(trades, date(2026, 7, 29), marks)
    assert curve[-1] == {"date": date(2026, 7, 29), "total": -0.05}


def test_equity_curve_folds_the_live_point_into_the_same_day():
    # A realized point already exists for today -> fold, don't emit two points.
    trades = trade_pnl.closed_trades([_entry(tkr="B", day="2026-07-29"),
                                      _exit(tkr="B", pnl=-0.25)])
    marks = [{"ticker": "OPEN", "entry_ask": 0.50, "count": 1, "bid": 0.70}]
    curve = trade_pnl.equity_curve(trades, date(2026, 7, 29), marks)
    assert [c["date"] for c in curve] == [date(2026, 7, 28), date(2026, 7, 29)]
    assert curve[-1]["total"] == -0.05


def test_equity_curve_holds_an_open_position_with_no_live_price_at_cost():
    # CHANGED behavior: an unquoted open position used to be dropped, which also
    # dropped today's live point. It is now held at cost, so the point is drawn —
    # at the same total, because a position marked at its entry contributes 0.
    trades = trade_pnl.closed_trades([_entry(tkr="B", day="2026-07-28"),
                                      _exit(tkr="B", pnl=-0.25)])
    marks = [{"ticker": "OPEN", "entry_ask": 0.50, "count": 1, "bid": None}]
    curve = trade_pnl.equity_curve(trades, date(2026, 7, 29), marks)
    assert curve[-1] == {"date": date(2026, 7, 29), "total": -0.25}


def test_equity_curve_live_point_alone_still_renders():
    # No closed trade yet, but an open marked position -> anchor + live point, so
    # the very first day of trading is visible instead of an empty chart.
    marks = [{"ticker": "OPEN", "entry_ask": 0.50, "count": 1, "bid": 0.70}]
    curve = trade_pnl.equity_curve([], date(2026, 7, 29), marks)
    assert curve == [{"date": date(2026, 7, 28), "total": 0.0},
                     {"date": date(2026, 7, 29), "total": 0.20}]


# --- Open contracts all count toward the total -------------------------------
# The boxes and the chart's live point are meant to cover EVERY open contract.
# A position with no live bid used to be dropped, which quietly shrank the set
# the total spoke for; it is now held at cost (contributing 0) until a bid
# appears — no invented number, but nothing silently missing either.

def test_unrealized_marks_an_open_position_to_the_bid():
    assert trade_pnl.unrealized(
        [{"entry_ask": 0.50, "count": 1, "bid": 0.70}]) == 0.20


def test_unrealized_scales_with_count():
    assert trade_pnl.unrealized(
        [{"entry_ask": 0.50, "count": 3, "bid": 0.70}]) == 0.60


def test_unrealized_holds_an_unquoted_position_at_cost():
    # No bid -> flat, not dropped and not a total loss.
    assert trade_pnl.unrealized([{"entry_ask": 0.44, "count": 1, "bid": None}]) == 0.0


def test_unrealized_counts_quoted_and_unquoted_together():
    out = trade_pnl.unrealized([{"entry_ask": 0.50, "count": 1, "bid": 0.70},
                                {"entry_ask": 0.44, "count": 1, "bid": None}])
    assert out == 0.20          # the unquoted one contributes 0, but is included


def test_unrealized_skips_a_position_with_no_cost_basis():
    # No entry_ask means nothing to mark AGAINST — marking at cost is undefined.
    assert trade_pnl.unrealized([{"entry_ask": None, "count": 1, "bid": 0.70}]) is None


def test_unrealized_of_nothing_is_none():
    assert trade_pnl.unrealized([]) is None
    assert trade_pnl.unrealized(None) is None


def test_equity_curve_live_point_includes_an_unquoted_position():
    trades = trade_pnl.closed_trades([_entry(tkr="B", day="2026-07-28"),
                                      _exit(tkr="B", pnl=-0.25)])
    marks = [{"ticker": "OPEN", "entry_ask": 0.50, "count": 1, "bid": 0.70},
             {"ticker": "DARK", "entry_ask": 0.44, "count": 1, "bid": None}]
    curve = trade_pnl.equity_curve(trades, date(2026, 7, 29), marks)
    assert curve[-1] == {"date": date(2026, 7, 29), "total": -0.05}
