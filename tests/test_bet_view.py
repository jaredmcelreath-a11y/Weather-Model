"""Import smoke for the My Bets page + a pure check on the equity chart helper
(the Streamlit render itself needs live credentials, so it's verified manually)."""

import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import bet_history

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

try:
    import cryptography  # noqa: F401
except ImportError:
    for _m in ("cryptography", "cryptography.hazmat", "cryptography.hazmat.primitives",
               "cryptography.hazmat.primitives.asymmetric"):
        sys.modules.setdefault(_m, MagicMock())


def test_bet_view_imports():
    import bet_view  # must import without side effects / missing names
    assert hasattr(bet_view, "render") and hasattr(bet_view, "equity_chart")


def test_model_cell_is_probability_only():
    import bet_view
    # No model read at all.
    assert bet_view._model_cell({"model_prob": None}) == "—"
    # Probability only now — edge/with-against moved out to tighten the column,
    # and it must not depend on edge/agree being present.
    assert bet_view._model_cell(
        {"model_prob": 0.62, "edge": None, "agree": None}) == "62%"
    assert bet_view._model_cell(
        {"model_prob": 0.62, "edge": 0.19, "agree": True}) == "62%"


def test_pct_gain_cell():
    import bet_view
    # Closed/settled: realized pnl ÷ staked.
    assert bet_view._pct_gain_cell(
        {"status": "settled", "pnl": 3.0, "staked": 6.0}) == "+50.0%"
    assert bet_view._pct_gain_cell(
        {"status": "closed", "pnl": -1.5, "staked": 6.0}) == "-25.0%"
    # Open: marked to market from current_value, shown live in terracotta with a ~.
    open_cell = bet_view._pct_gain_cell(
        {"status": "open", "entry": 0.50, "current_value": 0.60,
         "qty": 10.0, "staked": 5.0})
    assert "~" in open_cell and "+20.0%" in open_cell and "C97B5E" in open_cell
    # No staked / no mark → em dash, never a crash.
    assert bet_view._pct_gain_cell({"status": "settled", "pnl": 3.0, "staked": 0}) == "—"
    assert bet_view._pct_gain_cell(
        {"status": "open", "entry": None, "current_value": None,
         "qty": 1.0, "staked": 5.0}) == "—"


def test_equity_chart_encodes_date_and_total():
    import bet_view
    curve = [{"date": date(2026, 6, 23), "total": 5.8},
             {"date": date(2026, 6, 24), "total": 0.8}]
    spec = bet_view.equity_chart(curve, color="#7FD3A2").to_dict()
    # equity_chart layers a zero-baseline rule, the balance line, tappable dots, and a
    # pinned tap-to-read label. The date/total encoding lives on the line + dots layers
    # (the rule and the pixel-anchored text label don't carry an x field).
    dated = [L for L in spec["layer"]
             if L.get("encoding", {}).get("x", {}).get("field") == "date"]
    assert dated, "expected a layer encoding date on x"
    assert dated[0]["encoding"]["y"]["field"] == "total"
    # a click selection drives the tap-to-pin readout (mobile: no hover needed)
    assert any(isinstance(p.get("select"), dict) and p["select"].get("on") == "click"
               for p in spec.get("params", []))


def test_equity_chart_ships_datetimes_not_bare_date_strings():
    # Bare "2026-07-18" strings in a temporal encoding parse as UTC midnight in
    # the browser and render a day early for US viewers; naive datetimes
    # ("...T00:00:00") parse as local midnight and stay on the right day.
    import bet_view
    curve = [{"date": "2026-07-17", "total": 100.0},
             {"date": "2026-07-18", "total": 104.5}]
    spec = bet_view.equity_chart(curve, "#8bc34a").to_dict()
    checked = 0
    for ds in spec["datasets"].values():
        for row in ds:
            if "date" in row:
                assert "T" in str(row["date"]), row
                checked += 1
    assert checked, "expected at least one dated row in the shipped datasets"


# ---- Day grouping, subtotals and reconciliation -----------------------------
# Ported from the Screen page after two rounds of "the chart says X, the table
# says Y" there. Same defect lived here: the table dated rows by the UTC fill
# timestamp while the curve and the Daily tab bucket by weather day.

def _bet(ticker, status="settled", pnl=None, mark=None, entry=0.40, qty=10.0,
         bought=23, hour=15, fee=0.0, label="99 to 100"):
    return {"ticker": ticker, "label": label, "side": "yes", "entry": entry,
            "exit": 1.0 if status == "settled" else None, "qty": qty,
            "status": status, "pnl": pnl, "staked": entry * qty,
            "current_value": mark, "result": "yes", "fee": fee,
            "first_ts": datetime(2026, 6, bought, hour, tzinfo=timezone.utc),
            "settled_ts": None, "model_prob": None}


def test_day_column_is_the_market_day_not_the_utc_fill_date():
    import bet_view
    # 00:30Z on Jun 24 is 7:30pm CDT Jun 23 — the fill date read a day late, and
    # the market is Jun 23's anyway.
    row = bet_view._trade_cells(
        _bet("KXHIGHTDAL-26JUN23-B99", pnl=6.0, bought=24, hour=0))
    assert row["Day"] == "Jun 23"


def test_rows_group_by_market_day_with_a_subtotal_per_day():
    import bet_view
    rows = [_bet("KXHIGHTDAL-26JUN24-B97", pnl=0.20, bought=24, hour=18),
            _bet("KXHIGHTDAL-26JUN23-B99", pnl=0.05, bought=23, hour=10),
            # bought EARLIEST but it is the Jun 24 market: it belongs with Jun 24
            _bet("KXHIGHTDAL-26JUN24-B99", pnl=-0.16, bought=23, hour=9)]
    out = bet_view.trade_table_rows(rows, date(2026, 6, 25))
    days = [r["Day"] for r in out]
    assert days == ["Jun 24", "Jun 24", "<b>Jun 24 total</b>",
                    "Jun 23", "<b>Jun 23 total</b>"]
    subtotal = out[2]
    assert subtotal["P&L"] == "<b>+$0.04</b>"        # 0.20 + (-0.16)
    assert subtotal["Contract"] == "<b>2 bets</b>"


def test_a_days_subtotal_equals_that_days_step_on_the_curve():
    import bet_view
    rows = [_bet("KXHIGHTDAL-26JUN24-B97", pnl=0.20, bought=24),
            _bet("KXHIGHTDAL-26JUN24-B99", pnl=-0.16, bought=23),
            _bet("KXHIGHTDAL-26JUN23-B99", pnl=0.83, bought=23)]
    steps = {p["date"]: p["step"] for p
             in bet_history.with_steps(bet_history.equity_curve_live(
                 rows, date(2026, 6, 25)))}
    subs = {r["Day"]: r["P&L"] for r in
            bet_view.trade_table_rows(rows, date(2026, 6, 25))
            if r["Day"].startswith("<b>")}
    assert subs["<b>Jun 24 total</b>"] == f"<b>{bet_view._fmt_pnl(steps[date(2026, 6, 24)])}</b>"
    assert subs["<b>Jun 23 total</b>"] == f"<b>{bet_view._fmt_pnl(steps[date(2026, 6, 23)])}</b>"


def test_an_open_bet_subtotal_is_marked_as_a_live_number():
    import bet_view
    rows = [_bet("KXHIGHTDAL-26JUN24-B97", status="open", mark=0.55, entry=0.40)]
    sub = bet_view.trade_table_rows(rows, date(2026, 6, 25))[-1]
    assert sub["P&L"] == "<b>~+$1.50</b>"
    assert "1 open" in sub["Contract"]


def test_reconciliation_separates_the_fees_from_the_price_move():
    import bet_view
    rows = [_bet("KXHIGHTDAL-26JUN23-B99", pnl=5.84, fee=0.16)]
    days = bet_history.day_breakdown(rows, date(2026, 6, 25))
    out = bet_view.reconciliation_rows(days)
    assert (out[0]["Gross"], out[0]["Fees"], out[0]["Net"]) == (
        "+$6.00", "−$0.16", "+$5.84")
    assert out[1]["Contract"] == "<b>chart step +$5.84 ✓</b>"


def test_equity_curve_places_an_open_bet_on_its_own_market_day():
    # The behaviour change this port makes: open marks used to pile onto a single
    # point dated today, disagreeing with the Daily tab.
    rows = [_bet("KXHIGHTDAL-26JUN24-B97", status="open", mark=0.55, entry=0.40)]
    curve = bet_history.equity_curve_live(rows, date(2026, 6, 26))
    assert curve[-1]["date"] == date(2026, 6, 24)
    assert curve[-1]["open"] is True


def test_the_daily_tab_and_the_curve_agree_on_every_day():
    rows = [_bet("KXHIGHTDAL-26JUN24-B97", status="open", mark=0.55, entry=0.40),
            _bet("KXHIGHTDAL-26JUN23-B99", pnl=0.83, bought=23)]
    steps = {p["date"]: round(p["step"], 2) for p
             in bet_history.with_steps(
                 bet_history.equity_curve_live(rows, date(2026, 6, 26)))
             if p["step"]}
    daily = {e["label"]: round(e["gain"], 2)
             for e in bet_history.period_table(rows, "day")}
    assert steps == daily



def test_subtotal_rows_leave_unused_cells_blank_not_nan():
    # A DataFrame built with a fixed column list fills a missing key with NaN, and
    # the HTML table renders that as the literal 'nan' across half the row.
    import bet_view
    rows = [_bet("KXHIGHTDAL-26JUN23-B99", pnl=0.83)]
    sub = bet_view.trade_table_rows(rows, date(2026, 6, 25))[-1]
    assert set(sub) == set(bet_view.TRADE_COLUMNS)
    assert sub["City"] == "" and sub["Entry"] == "" and sub["Side"] == ""


def test_reconciliation_subtotal_rows_are_also_fully_populated():
    import bet_view
    rows = [_bet("KXHIGHTDAL-26JUN23-B99", pnl=0.83, fee=0.1)]
    sub = bet_view.reconciliation_rows(
        bet_history.day_breakdown(rows, date(2026, 6, 25)))[-1]
    assert set(sub) == set(bet_view.RECON_COLUMNS)
    assert sub["Bought"] == "" and sub["Qty"] == ""
