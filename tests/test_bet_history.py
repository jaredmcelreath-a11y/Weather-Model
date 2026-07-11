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
    "KXHIGHTDAL-26JUN22-B99": {"label": "99 to 100", "floor": 99, "cap": 100,
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
    # unweighted per-trade mean: (+5.80/4.20 - 5.00/5.00)/2 = (+138.1% - 100%)/2 ≈ +19.0%,
    # distinct from the stake-weighted roi (0.80/9.20 ≈ +8.7%)
    assert round(s["avg_trade_return"], 1) == 19.0
    assert round(s["roi"], 1) == 8.7
    curve = bh.equity_curve(rows)
    # Dated by WEATHER day (from the ticker), not the next-morning settlement.
    assert [c["date"] for c in curve] == [date(2026, 6, 22), date(2026, 6, 23)]
    # curve tracks account BALANCE from the $10 starting bankroll
    assert round(curve[0]["total"], 2) == 15.80       # 10 + 5.80
    assert round(curve[1]["total"], 2) == 10.80       # 10 + 5.80 - 5.00


def test_curve_dates_by_weather_day_not_settlement_day():
    # A bet for Jun 22 weather settles the morning of Jun 23; it must plot on Jun 22.
    fills = [_fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.50, 22)]  # loss
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "no", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc),
                    "revenue": 0.0}}
    curve = bh.equity_curve(bh.build_rows(fills, settlements, META))
    assert curve[0]["date"] == date(2026, 6, 22)      # weather day, NOT settlement day Jun 23
    assert round(curve[0]["total"], 2) == 5.00        # 10 bankroll - 5.00 loss (goes DOWN)


def test_equity_curve_aggregates_same_day_bets_into_one_point():
    # Two bets for the SAME weather day collapse into a single curve point (end-of-day
    # total), instead of two points stacked at the same x.
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22),  # +5.80
        _fill("t2", "KXHIGHTDAL-26JUN22-B99", "yes", "buy", 10, 0.50, 22),  # -5.00
    ]
    settlements = {
        "KXHIGHTDAL-26JUN22-B97": {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc), "revenue": 10.0},
        "KXHIGHTDAL-26JUN22-B99": {"result": "no", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc), "revenue": 0.0},
    }
    curve = bh.equity_curve(bh.build_rows(fills, settlements, META))
    assert len(curve) == 1
    assert curve[0]["date"] == date(2026, 6, 22)
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
    # realized Jun 22 (weather-day) balance = 15.80; live point today (Jun 24) adds +1.00 -> 16.80
    curve = bh.equity_curve_live(rows, date(2026, 6, 24))
    assert curve[-1]["date"] == date(2026, 6, 24)
    assert round(curve[-1]["total"], 2) == 16.80


def test_equity_curve_live_no_today_point_without_open_exposure():
    # With no open positions, the live curve is just the realized weather-day points —
    # it does not tack on a flat point at `today`.
    fills = [_fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22)]
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc),
                    "revenue": 10.0}}
    rows = bh.build_rows(fills, settlements, META)
    curve = bh.equity_curve_live(rows, date(2026, 6, 24))
    assert [c["date"] for c in curve] == [date(2026, 6, 22)]
    assert round(curve[0]["total"], 2) == 15.80


def test_closed_by_early_sell_is_realized_not_open():
    # Bought 10 @ $0.50, sold all 10 @ $0.70, market NOT settled yet -> realized +$2.00,
    # classified 'closed' (not a perpetual 'open' bet) and counted in the totals + curve.
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.50, 22),
        _fill("t2", "KXHIGHTDAL-26JUN22-B97", "yes", "sell", 10, 0.70, 22),
    ]
    rows = bh.build_rows(fills, {}, META)          # no settlements
    r = rows[0]
    assert r["status"] == "closed" and r["qty"] == 10
    assert round(r["pnl"], 2) == 2.00              # 7.00 sell - 5.00 buy
    s = bh.summary(rows)
    assert s["n_settled"] == 1 and s["wins"] == 1
    assert round(s["net_pnl"], 2) == 2.00
    assert round(s["roi"], 1) == 40.0              # 2.00 / 5.00 staked
    curve = bh.equity_curve(rows)                  # realized -> appears on the curve
    assert [c["date"] for c in curve] == [date(2026, 6, 22)]
    assert round(curve[0]["total"], 2) == 12.00    # 10 bankroll + 2.00


def test_sold_out_with_settlement_record_is_closed_not_settled():
    # July 10 bug: bought 3 YES @ $0.60, sold all 3 @ $0.99 BEFORE settlement, but Kalshi
    # still returns a settlement record for the market (revenue 0 — you held nothing at
    # expiry). Must be classified 'closed' (shows "sold") with qty = the size traded, NOT
    # 'settled' with qty 0 (buy_ct - sell_ct). P&L comes from the sells.
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 3, 0.60, 22),
        _fill("t2", "KXHIGHTDAL-26JUN22-B97", "yes", "sell", 3, 0.99, 22),
    ]
    stl = {"KXHIGHTDAL-26JUN22-B97":
           {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc),
            "revenue": 0.0}}
    r = bh.build_rows(fills, stl, META)[0]
    assert r["status"] == "closed" and r["result"] is None      # -> "sold", not "YES"
    assert r["qty"] == 3                                         # not 0
    assert round(r["exit"], 2) == 0.99                           # the market sell price
    assert round(r["pnl"], 2) == 1.17                            # 2.97 sell - 1.80 buy


def test_partial_open_position_still_open():
    # Bought 10, sold only 4, no settlement -> still net-long 6, stays OPEN (no realized
    # P&L yet), so the early-sell rule must not fire on a partial exit.
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.50, 22),
        _fill("t2", "KXHIGHTDAL-26JUN22-B97", "yes", "sell", 4, 0.70, 22),
    ]
    rows = bh.build_rows(fills, {}, META)
    assert rows[0]["status"] == "open" and rows[0]["pnl"] is None


def test_cross_side_sell_close_is_realized():
    # Kalshi records closing a YES position as a SELL on the NO side (the real fill schema).
    # Net-held must subtract ALL sells, not just same-side, so the position goes flat.
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 3.24, 0.60, 22),
        _fill("t2", "KXHIGHTDAL-26JUN22-B97", "no", "sell", 3.24, 0.01, 22),
    ]
    rows = bh.build_rows(fills, {}, META)
    r = rows[0]
    assert r["status"] == "closed" and round(r["qty"], 2) == 3.24
    # realized at the dominant (yes) 0.99 price: 3.24*0.99 - 3.24*0.60 = +1.26
    assert round(r["pnl"], 2) == 1.26
    assert round(bh.summary(rows)["net_pnl"], 2) == 1.26


def test_summary_marks_open_positions_to_market():
    # A settled win (+5.80) plus an OPEN position marked to market (+1.00 unrealized).
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
            r["current_value"] = 0.60          # entry 0.40 × qty 5 -> +$1.00 unrealized
    s = bh.summary(rows)
    assert round(s["net_pnl"], 2) == 6.80      # realized 5.80 + open MTM 1.00
    assert round(s["realized_pnl"], 2) == 5.80
    assert round(s["pct_gain"], 0) == 68       # 6.80 / $10 bankroll
    # win/loss + record stay realized-only (the open bet hasn't won or lost yet)
    assert s["wins"] == 1 and s["losses"] == 0 and s["n_settled"] == 1


def test_period_table_daily_weekly_monthly():
    fills = [
        _fill("a", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.50, 22),  # +5.00
        _fill("b", "KXHIGHTDAL-26JUN22-B99", "yes", "buy", 10, 0.40, 22),  # -4.00
        _fill("c", "KXHIGHTDAL-26JUN29-B97", "yes", "buy", 10, 0.30, 29),  # +7.00
    ]
    stl = {
        "KXHIGHTDAL-26JUN22-B97": {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc), "revenue": 10.0},
        "KXHIGHTDAL-26JUN22-B99": {"result": "no", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc), "revenue": 0.0},
        "KXHIGHTDAL-26JUN29-B97": {"result": "yes", "ts": datetime(2026, 6, 30, 6, tzinfo=timezone.utc), "revenue": 10.0},
    }
    rows = bh.build_rows(fills, stl, META)
    daily = bh.period_table(rows, "day")
    # Jun 22: +5-4=+1 on $9 staked -> total 11; Jun 29: +7 on $3 -> total 18
    assert [(d["label"].isoformat(), round(d["gain"], 2), round(d["total"], 2)) for d in daily] == \
        [("2026-06-22", 1.00, 11.00), ("2026-06-29", 7.00, 18.00)]
    assert round(daily[0]["pct"], 3) == 0.111
    weekly = bh.period_table(rows, "week")
    from datetime import date as _d, timedelta as _td
    mons = [_d(2026, 6, x) - _td(days=_d(2026, 6, x).weekday()) for x in (22, 29)]
    assert [w["label"] for w in weekly] == mons and len(weekly) == 2
    monthly = bh.period_table(rows, "month")
    assert len(monthly) == 1 and monthly[0]["label"] == _d(2026, 6, 1)
    assert round(monthly[0]["gain"], 2) == 8.00 and round(monthly[0]["total"], 2) == 18.00


def test_period_summary_over_multiple_periods():
    # Three periods: a win, a flat $0 (not green), and a loss.
    entries = [
        {"label": date(2026, 6, 22), "pct": 0.20, "gain": 4.0, "total": 14.0},
        {"label": date(2026, 6, 23), "pct": 0.00, "gain": 0.0, "total": 14.0},
        {"label": date(2026, 6, 24), "pct": -0.10, "gain": -2.0, "total": 12.0},
    ]
    s = bh.period_summary(entries, 180.0)
    assert s["count"] == 3
    assert round(s["avg_gain"], 4) == round((4.0 + 0.0 - 2.0) / 3, 4)
    assert round(s["avg_pct"], 4) == round((0.20 + 0.00 - 0.10) / 3, 4)
    assert s["green_count"] == 1                      # flat $0 is NOT green
    assert round(s["green_rate"], 4) == round(1 / 3, 4)
    assert s["best_gain"] == 4.0 and s["worst_gain"] == -2.0
    assert s["pct_gain"] == 180.0                     # passthrough, marked-to-market


def test_period_summary_single_period():
    entries = [{"label": date(2026, 6, 22), "pct": 0.05, "gain": 1.5, "total": 11.5}]
    s = bh.period_summary(entries, 15.0)
    assert s["count"] == 1
    assert s["best_gain"] == s["worst_gain"] == 1.5
    assert s["green_count"] == 1 and s["green_rate"] == 1.0
    assert round(s["avg_gain"], 4) == 1.5 and round(s["avg_pct"], 4) == 0.05


def test_period_summary_empty_returns_none():
    assert bh.period_summary([], 0.0) is None
