"""Unit tests for the Screen page's earnings history — membership, the cumulative
P&L curve, and the summary. Pure: inputs are already-normalized fills/settlements
dicts, no Kalshi and no network."""

from datetime import date, datetime, timezone

import pytest

import screen_pnl


def _fill(ticker, side, action, count, price, day, hour=19, tid=None):
    """One normalized Kalshi fill. yes/no prices are complementary; `price` is the
    fill's own-side price (the shape bet_history.build_rows consumes)."""
    yes_p = price if side == "yes" else round(1 - price, 4)
    no_p = round(1 - price, 4) if side == "yes" else price
    return {"trade_id": tid or f"{ticker}-{action}-{count}", "ticker": ticker,
            "side": side, "action": action, "count": count, "price": price,
            "yes_price": yes_p, "no_price": no_p, "fee": 0.0,
            "ts": datetime(2026, 8, day, hour, tzinfo=timezone.utc)}


def _settle(result, day, revenue, hour=6):
    return {"result": result, "revenue": revenue,
            "ts": datetime(2026, 8, day, hour, tzinfo=timezone.utc)}


DEN_HIGH = "KXHIGHDEN-26AUG03-T95"
DEN_LOW = "KXLOWTDEN-26AUG04-B62"
PHIL_HIGH = "KXHIGHPHIL-26AUG04-T90"


# ---- membership ------------------------------------------------------------

def test_denver_high_and_low_are_screen_tickers():
    assert screen_pnl.is_screen_ticker(DEN_HIGH)
    assert screen_pnl.is_screen_ticker(DEN_LOW)


@pytest.mark.parametrize("ticker", ["KXHIGHTDAL-26AUG03-B97",
                                    "KXLOWTDAL-26AUG03-B77",
                                    "KXHIGHAUS-26AUG03-B99",
                                    "KXLOWTAUS-26AUG03-B76"])
def test_the_cities_this_app_models_are_excluded(ticker):
    """Dallas and Austin have their own History page; counting them here would
    show the same trade in two places."""
    assert not screen_pnl.is_screen_ticker(ticker)


@pytest.mark.parametrize("ticker", ["KXRAINDEN-26AUG03-T1", "NOTASERIES-X",
                                    "", None])
def test_a_series_the_screen_does_not_cover_is_excluded(ticker):
    assert not screen_pnl.is_screen_ticker(ticker)


def test_trade_rows_keep_only_screened_cities():
    fills = [_fill(DEN_HIGH, "no", "buy", 10, 0.30, 3),
             _fill("KXHIGHTDAL-26AUG03-B97", "yes", "buy", 5, 0.60, 3)]
    rows = screen_pnl.trade_rows(fills, {}, {}, lambda t, s: None)
    assert [r["ticker"] for r in rows] == [DEN_HIGH]


def test_trade_rows_mark_open_positions_and_label_from_meta():
    fills = [_fill(DEN_HIGH, "no", "buy", 10, 0.30, 3)]
    meta = {DEN_HIGH: {"label": "95° or above", "variable": "high"}}
    rows = screen_pnl.trade_rows(fills, {}, meta, lambda t, s: 0.44)
    assert rows[0]["status"] == "open"
    assert rows[0]["current_value"] == 0.44
    assert rows[0]["label"] == "95° or above"


def test_trade_rows_leave_settled_rows_unmarked():
    """A settled row's P&L is realized; marking it to a live bid would be wrong
    (and the market has no bid any more anyway)."""
    fills = [_fill(DEN_HIGH, "no", "buy", 10, 0.30, 3)]
    setts = {DEN_HIGH: _settle("no", 4, 10.0)}
    rows = screen_pnl.trade_rows(fills, setts, {}, lambda t, s: 0.99)
    assert rows[0]["status"] == "settled"
    assert rows[0].get("current_value") is None
    assert rows[0]["pnl"] == pytest.approx(7.0)


def test_trade_rows_survive_a_mark_that_fails():
    """One unpriceable ticker must not empty the whole table."""
    def boom(ticker, side):
        raise RuntimeError("no quote")
    rows = screen_pnl.trade_rows([_fill(DEN_HIGH, "no", "buy", 10, 0.30, 3)],
                                 {}, {}, boom)
    assert rows[0]["current_value"] is None


# ---- earnings curve -------------------------------------------------------

def _settled_row(ticker, pnl, staked=3.0):
    return {"ticker": ticker, "status": "settled", "pnl": pnl, "staked": staked,
            "qty": 10, "entry": 0.3, "settled_ts": None}


def _open_row(ticker, entry=0.30, mark=0.45, qty=10, staked=3.0):
    return {"ticker": ticker, "status": "open", "pnl": None, "staked": staked,
            "qty": qty, "entry": entry, "current_value": mark,
            "settled_ts": None}


def test_curve_starts_from_zero_not_a_bankroll():
    rows = [_settled_row(DEN_HIGH, 7.0)]
    curve = screen_pnl.earnings_curve(rows, date(2026, 8, 4))
    assert curve[0] == {"date": date(2026, 8, 2), "total": 0.0,   # anchor
                        "unrealized": 0.0, "open": False}
    assert curve[-1]["total"] == pytest.approx(7.0)


def test_curve_buckets_by_weather_day_not_settlement_day():
    """These markets settle ~1-2am the next morning, so settlement time would
    plot every day's result a day late."""
    rows = [dict(_settled_row(DEN_HIGH, 7.0),
                 settled_ts=datetime(2026, 8, 4, 7, tzinfo=timezone.utc))]
    curve = screen_pnl.earnings_curve(rows, date(2026, 8, 5))
    assert [p["date"] for p in curve] == [date(2026, 8, 2), date(2026, 8, 3)]


def test_two_trades_on_one_weather_day_make_one_point():
    rows = [_settled_row(DEN_HIGH, 7.0),
            _settled_row("KXLOWTDEN-26AUG03-B62", -3.0)]
    curve = screen_pnl.earnings_curve(rows, date(2026, 8, 4))
    assert len(curve) == 2                      # anchor + the one trading day
    assert curve[-1]["total"] == pytest.approx(4.0)


def test_curve_runs_cumulatively_across_days():
    rows = [_settled_row(DEN_HIGH, 7.0), _settled_row(DEN_LOW, -2.0)]
    curve = screen_pnl.earnings_curve(rows, date(2026, 8, 5))
    assert [round(p["total"], 2) for p in curve] == [0.0, 7.0, 5.0]


def test_an_open_position_plots_on_the_day_its_own_market_resolves():
    # NOT on today: a bracket bought for a later day belongs on that day, or the
    # line bulges wherever you happen to be standing.
    rows = [_settled_row(DEN_HIGH, 7.0),                 # weather day Aug 3
            _open_row(PHIL_HIGH, entry=0.30, mark=0.45)]  # weather day Aug 4
    curve = screen_pnl.earnings_curve(rows, date(2026, 8, 6))
    assert [p["date"] for p in curve] == [date(2026, 8, 2), date(2026, 8, 3),
                                          date(2026, 8, 4)]
    assert curve[-1]["total"] == pytest.approx(8.5)      # 7.0 + 10*(0.45-0.30)


def test_an_open_day_is_flagged_with_how_much_of_it_is_a_mark():
    # What lets the chart draw that stretch dashed rather than implying the money
    # is banked.
    rows = [_settled_row(DEN_HIGH, 7.0), _open_row(PHIL_HIGH, 0.30, 0.45)]
    realized, live = screen_pnl.earnings_curve(rows, date(2026, 8, 6))[1:]
    assert (realized["open"], realized["unrealized"]) == (False, 0.0)
    assert (live["open"], live["unrealized"]) == (True, pytest.approx(1.5))


def test_an_open_position_for_a_future_day_extends_the_line_to_that_day():
    rows = [_settled_row(DEN_HIGH, 7.0),
            _open_row("KXHIGHPHIL-26AUG09-T90", 0.30, 0.40)]
    curve = screen_pnl.earnings_curve(rows, date(2026, 8, 6))
    assert curve[-1]["date"] == date(2026, 8, 9)
    assert curve[-1]["open"] is True


def test_an_open_position_with_an_unreadable_ticker_falls_back_to_today():
    rows = [_open_row("NOT-A-DATE", 0.30, 0.45)]
    curve = screen_pnl.earnings_curve(rows, date(2026, 8, 6))
    assert curve[-1]["date"] == date(2026, 8, 6)


def test_a_realized_and_an_open_trade_on_one_day_make_a_single_point():
    """Two positions about the same weather day are one step on the line, and the
    step counts as open because part of it is still a mark."""
    today = date(2026, 8, 4)
    rows = [_settled_row(DEN_LOW, 4.0),                 # weather day Aug 4
            _open_row("KXHIGHPHIL-26AUG04-T90", 0.30, 0.45)]
    curve = screen_pnl.earnings_curve(rows, today)
    assert [p["date"] for p in curve] == [date(2026, 8, 3), today]
    assert curve[-1]["total"] == pytest.approx(5.5)
    assert (curve[-1]["open"], curve[-1]["unrealized"]) == (True,
                                                            pytest.approx(1.5))


def test_curve_of_nothing_is_empty():
    assert screen_pnl.earnings_curve([], date(2026, 8, 5)) == []


def test_an_open_position_with_no_mark_still_yields_a_curve():
    # No live bid means no number to plot; the realized history must survive it.
    rows = [_settled_row(DEN_HIGH, 7.0), _open_row(PHIL_HIGH, 0.30, None)]
    curve = screen_pnl.earnings_curve(rows, date(2026, 8, 6))
    assert curve[-1]["total"] == pytest.approx(7.0)
    assert curve[-1]["open"] is False


# ---- summary --------------------------------------------------------------

def test_summary_counts_wins_and_losses_realized_only():
    rows = [_settled_row(DEN_HIGH, 7.0), _settled_row(DEN_LOW, -3.0),
            {"ticker": PHIL_HIGH, "status": "open", "pnl": None, "staked": 3.0,
             "qty": 10, "entry": 0.30, "current_value": 0.45, "settled_ts": None}]
    s = screen_pnl.summary(rows)
    assert (s["wins"], s["losses"], s["n_settled"]) == (1, 1, 2)
    assert s["win_rate"] == pytest.approx(50.0)


def test_summary_net_pnl_includes_open_marks():
    rows = [_settled_row(DEN_HIGH, 7.0),
            {"ticker": PHIL_HIGH, "status": "open", "pnl": None, "staked": 3.0,
             "qty": 10, "entry": 0.30, "current_value": 0.45, "settled_ts": None}]
    s = screen_pnl.summary(rows)
    assert s["net_pnl"] == pytest.approx(8.5)
    assert s["realized_pnl"] == pytest.approx(7.0)
    assert s["staked"] == pytest.approx(6.0)
    assert s["roi"] == pytest.approx(100.0 * 8.5 / 6.0)


def test_summary_of_nothing_is_zeroed_not_an_error():
    s = screen_pnl.summary([])
    assert (s["wins"], s["losses"], s["net_pnl"], s["win_rate"]) == (0, 0, 0, 0.0)
    assert s["roi"] == 0.0


def test_median_trade_return_ignores_the_wipeout_tail():
    """Fading favorites pairs many small wins with rare −100% losses; the mean of
    those washes to ~0 even when the strategy is profitable."""
    rows = [_settled_row(DEN_HIGH, 0.6, staked=6.0),        # +10%
            _settled_row(DEN_LOW, 0.6, staked=6.0),         # +10%
            _settled_row(PHIL_HIGH, -6.0, staked=6.0)]      # −100%
    s = screen_pnl.summary(rows)
    assert s["median_trade_return"] == pytest.approx(10.0)
