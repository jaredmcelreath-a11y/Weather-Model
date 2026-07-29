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


def test_equity_curve_skips_an_open_position_with_no_live_price():
    trades = trade_pnl.closed_trades([_entry(tkr="B", day="2026-07-28"),
                                      _exit(tkr="B", pnl=-0.25)])
    marks = [{"ticker": "OPEN", "entry_ask": 0.50, "count": 1, "bid": None}]
    curve = trade_pnl.equity_curve(trades, date(2026, 7, 29), marks)
    assert curve[-1] == {"date": date(2026, 7, 28), "total": -0.25}


def test_equity_curve_live_point_alone_still_renders():
    # No closed trade yet, but an open marked position -> anchor + live point, so
    # the very first day of trading is visible instead of an empty chart.
    marks = [{"ticker": "OPEN", "entry_ask": 0.50, "count": 1, "bid": 0.70}]
    curve = trade_pnl.equity_curve([], date(2026, 7, 29), marks)
    assert curve == [{"date": date(2026, 7, 28), "total": 0.0},
                     {"date": date(2026, 7, 29), "total": 0.20}]
