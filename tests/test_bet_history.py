"""Unit tests for bet assembly, P&L, summary, and the equity curve. Pure — inputs
are the already-normalized fills/settlements/meta dicts (no Kalshi, no network)."""

from datetime import date, datetime, timezone

import bet_history as bh


def _fill(tid, ticker, side, action, count, price, day, hour=19):
    return {"trade_id": tid, "ticker": ticker, "variable": "high", "side": side,
            "action": action, "count": count, "price": price,
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
                   {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc)}}
    rows = bh.build_rows(fills, settlements, META)
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "yes" and r["qty"] == 10 and r["entry"] == 0.42
    assert r["status"] == "settled" and r["result"] == "yes"
    # 10 bought @0.42 -> cash_flow -4.20; settled yes -> payout +10; pnl +5.80
    assert round(r["pnl"], 2) == 5.80


def test_build_rows_settled_loss_pnl():
    fills = [_fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22)]
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "no", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc)}}
    r = bh.build_rows(fills, settlements, META)[0]
    assert round(r["pnl"], 2) == -4.20                # lost the stake


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
        "KXHIGHTDAL-26JUN22-B97": {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc)},
        "KXHIGHTDAL-26JUN23-B99": {"result": "no", "ts": datetime(2026, 6, 24, 6, tzinfo=timezone.utc)},
    }
    rows = bh.build_rows(fills, settlements, META)
    s = bh.summary(rows)
    assert s["n_settled"] == 2 and s["wins"] == 1 and s["losses"] == 1
    assert s["win_rate"] == 50.0
    assert round(s["net_pnl"], 2) == 0.80             # +5.80 - 5.00
    assert round(s["staked"], 2) == 9.20              # 4.20 + 5.00
    curve = bh.equity_curve(rows)
    assert [c["date"] for c in curve] == [date(2026, 6, 23), date(2026, 6, 24)]
    assert round(curve[0]["total"], 2) == 5.80
    assert round(curve[1]["total"], 2) == 0.80        # cumulative
