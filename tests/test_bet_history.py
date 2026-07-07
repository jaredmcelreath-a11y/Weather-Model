"""Unit tests for bet assembly, P&L, summary, and the equity curve. Pure — inputs
are the already-normalized fills/settlements/meta dicts (no Kalshi, no network)."""

from datetime import date, datetime, timezone

import bet_history as bh


def _fill(tid, ticker, side, action, count, price, day, hour=19):
    # yes/no prices are complementary; `price` is the fill's own-side price.
    yes_p = price if side == "yes" else round(1 - price, 4)
    no_p = round(1 - price, 4) if side == "yes" else price
    return {"trade_id": tid, "ticker": ticker, "variable": "high", "side": side,
            "action": action, "count": count, "price": price,
            "yes_price": yes_p, "no_price": no_p,
            "ts": datetime(2026, 6, day, hour, tzinfo=timezone.utc)}


META = {
    "KXHIGHTDAL-26JUN22-B97": {"label": "97 to 98", "floor": 97, "cap": 98,
                               "strike_type": "between", "variable": "high"},
    "KXHIGHTDAL-26JUN23-B99": {"label": "99 to 100", "floor": 99, "cap": 100,
                               "strike_type": "between", "variable": "high"},
}


def test_build_rows_settled_win_pnl_and_fields():
    fills = [_fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22)]
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc),
                    "revenue": 10.0}}                     # held 10 YES -> $10 payout
    rows = bh.build_rows(fills, settlements, META)
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "yes" and r["qty"] == 10 and r["entry"] == 0.42
    assert r["status"] == "settled" and r["result"] == "yes"
    assert r["exit"] == 1.0                            # held to settlement, won -> $1
    # 10 bought @0.42 -> cash_flow -4.20; settled yes -> payout +10; pnl +5.80
    assert round(r["pnl"], 2) == 5.80


def test_build_rows_settled_loss_pnl():
    fills = [_fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22)]
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "no", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc),
                    "revenue": 0.0}}                      # held YES, lost -> $0 payout
    r = bh.build_rows(fills, settlements, META)[0]
    assert round(r["pnl"], 2) == -4.20                # lost the stake
    assert r["exit"] == 0.0                            # held to settlement, lost -> $0


def test_build_rows_pnl_with_a_partial_sell():
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22),
        _fill("t2", "KXHIGHTDAL-26JUN22-B97", "yes", "sell", 4, 0.50, 22),
    ]
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc),
                    "revenue": 6.0}}                      # 6 YES held to settlement -> $6
    rows = bh.build_rows(fills, settlements, META)
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "yes"
    assert r["qty"] == 6                      # net YES after the sell (10 - 4)
    assert r["entry"] == 0.42                 # avg BUY price of the yes side (4.20/10)
    assert r["exit"] == 0.50                  # avg SELL price of the yes side
    assert round(r["staked"], 2) == 4.20      # buy cost of the yes side
    # cash_flow = 2.00 (sell) - 4.20 (buy) = -2.20; payout = net_yes 6 x $1 = 6.00
    assert round(r["pnl"], 2) == 3.80


def test_open_bet_has_no_pnl_and_is_excluded_from_curve():
    fills = [_fill("t1", "KXHIGHTDAL-26JUN23-B99", "yes", "buy", 4, 0.30, 23)]
    rows = bh.build_rows(fills, {}, META)
    assert rows[0]["status"] == "open" and rows[0]["pnl"] is None
    assert bh.equity_curve(rows) == []


def test_summary_and_curve_across_two_settled_bets():
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22),  # +5.80
        _fill("t2", "KXHIGHTDAL-26JUN23-B99", "yes", "buy", 10, 0.50, 23),  # -5.00 (loss)
    ]
    settlements = {
        "KXHIGHTDAL-26JUN22-B97": {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc), "revenue": 10.0},
        "KXHIGHTDAL-26JUN23-B99": {"result": "no", "ts": datetime(2026, 6, 24, 6, tzinfo=timezone.utc), "revenue": 0.0},
    }
    rows = bh.build_rows(fills, settlements, META)
    s = bh.summary(rows)
    assert s["n_settled"] == 2 and s["wins"] == 1 and s["losses"] == 1
    assert s["win_rate"] == 50.0
    assert round(s["net_pnl"], 2) == 0.80             # +5.80 - 5.00
    assert round(s["staked"], 2) == 9.20              # 4.20 + 5.00
    assert round(s["pct_gain"], 1) == 8.0             # net 0.80 / $10 bankroll
    curve = bh.equity_curve(rows)
    assert [c["date"] for c in curve] == [date(2026, 6, 23), date(2026, 6, 24)]
    # curve tracks account BALANCE from the $10 starting bankroll
    assert round(curve[0]["total"], 2) == 15.80       # 10 + 5.80
    assert round(curve[1]["total"], 2) == 10.80       # 10 + 5.80 - 5.00


def test_equity_curve_aggregates_same_day_bets_into_one_point():
    # Two bets that both settle on the SAME day collapse into a single curve point
    # (end-of-day total), instead of two points stacked at the same x.
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22),  # +5.80
        _fill("t2", "KXHIGHTDAL-26JUN23-B99", "yes", "buy", 10, 0.50, 23),  # -5.00
    ]
    same_day = datetime(2026, 6, 24, 6, tzinfo=timezone.utc)
    settlements = {
        "KXHIGHTDAL-26JUN22-B97": {"result": "yes", "ts": same_day, "revenue": 10.0},
        "KXHIGHTDAL-26JUN23-B99": {"result": "no", "ts": same_day, "revenue": 0.0},
    }
    curve = bh.equity_curve(bh.build_rows(fills, settlements, META))
    assert len(curve) == 1
    assert curve[0]["date"] == date(2026, 6, 24)
    assert round(curve[0]["total"], 2) == 10.80         # 10 + (5.80 - 5.00), one point


def test_open_unrealized_and_live_curve_point():
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22),   # settled +5.80
        _fill("t2", "KXHIGHTDAL-26JUN23-B99", "yes", "buy", 5, 0.40, 23),    # open
    ]
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc),
                    "revenue": 10.0}}
    rows = bh.build_rows(fills, settlements, META)
    for r in rows:
        if r["status"] == "open":
            r["current_value"] = 0.60                 # entry 0.40, qty 5 -> +$1.00 unreal
    assert round(bh.open_unrealized(rows), 2) == 1.00
    # realized Jun 23 balance = 15.80; live point today (Jun 24) adds +1.00 -> 16.80
    curve = bh.equity_curve_live(rows, date(2026, 6, 24))
    assert curve[-1]["date"] == date(2026, 6, 24)
    assert round(curve[-1]["total"], 2) == 16.80


def test_equity_curve_live_folds_into_today_realized_point():
    fills = [_fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22),
             _fill("t2", "KXHIGHTDAL-26JUN23-B99", "yes", "buy", 5, 0.40, 23)]
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc),
                    "revenue": 10.0}}
    rows = bh.build_rows(fills, settlements, META)
    for r in rows:
        if r["status"] == "open":
            r["current_value"] = 0.60
    curve = bh.equity_curve_live(rows, date(2026, 6, 23))   # today == the settled date
    assert len(curve) == 1
    assert curve[0]["date"] == date(2026, 6, 23) and round(curve[0]["total"], 2) == 16.80
