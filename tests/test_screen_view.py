from datetime import date, datetime, timedelta, timezone

import pytest

import screen_pnl
import screen_view


def _c(ts, ticker, price, gap, kind="forecast"):
    return {"ts": ts, "series": "KXLOWTDEN", "variable": "low",
            "ticker": ticker, "floor": 72, "cap": 73, "price": price,
            "forecast": 66.0, "gap": gap, "kind": kind, "hours_to_close": 11.0}


def test_latest_firing_keeps_only_the_newest_timestamp():
    rows = [_c("2026-08-03T12:00:00Z", "a", 0.2, 5.0),
            _c("2026-08-03T18:00:00Z", "b", 0.3, 6.0),
            _c("2026-08-03T18:00:00Z", "c", 0.4, 7.0)]
    got = screen_view.latest_firing(rows)
    assert {r["ticker"] for r in got} == {"b", "c"}


def test_latest_firing_of_nothing_is_empty():
    assert screen_view.latest_firing([]) == []


def test_display_rows_put_the_soonest_close_first():
    # Urgency beats size: a bracket closing in 3h needs a decision now, however
    # juicy a 30h-away one looks.
    rows = [dict(_c("t", "far", 0.40, 8.0), hours_to_close=30.0),
            dict(_c("t", "soon", 0.15, 4.0), hours_to_close=3.0),
            dict(_c("t", "mid", 0.30, 5.0), hours_to_close=12.0)]
    got = screen_view.display_rows(rows)
    assert [r["ticker"] for r in got] == ["soon", "mid", "far"]


def test_display_rows_sort_a_missing_hours_last():
    rows = [dict(_c("t", "unknown", 0.40, 8.0), hours_to_close=None),
            dict(_c("t", "soon", 0.30, 5.0), hours_to_close=2.0)]
    got = screen_view.display_rows(rows)
    assert [r["ticker"] for r in got] == ["soon", "unknown"]


def test_display_uses_the_city_name_not_the_series_ticker():
    row = _c("t", "x", 0.4, 5.0)
    row["series"] = "KXHIGHPHIL"
    assert screen_view.city_of(row) == "Philadelphia"


def test_display_falls_back_to_the_series_when_unmapped():
    row = _c("t", "x", 0.4, 5.0)
    row["series"] = "KXHIGHNOWHERE"
    assert screen_view.city_of(row) == "KXHIGHNOWHERE"


def _row_hrs(hours, variable="low", kind="forecast"):
    r = _c("t", "x", 0.3, 5.0, kind)
    r["hours_to_close"] = hours
    r["variable"] = variable
    return r


def test_side_is_always_no_because_the_screen_only_finds_overpriced_brackets():
    assert screen_view.side_of(_row_hrs(10.0)) == "NO"
    assert screen_view.side_of(_row_hrs(10.0, kind="dead")) == "NO"


def test_a_future_climate_day_is_not_settled():
    # >24h to close means the day has not begun; nothing is determined.
    assert screen_view.settled_of(_row_hrs(27.9, "high")) == "No"
    assert screen_view.settled_of(_row_hrs(28.9, "low")) == "No"


def test_a_low_is_settled_once_the_dawn_window_has_passed():
    # Day runs 24h to close -> 0. A low forms early, so by 6h left it is in.
    assert screen_view.settled_of(_row_hrs(5.9, "low")) == "Yes"
    assert screen_view.settled_of(_row_hrs(20.0, "low")) == "No"


def test_a_high_needs_the_afternoon_before_it_is_settled():
    # The high peaks late, so it stays unsettled far longer than the low.
    assert screen_view.settled_of(_row_hrs(12.0, "high")) == "No"
    assert screen_view.settled_of(_row_hrs(5.0, "high")) == "Yes"


def test_a_dead_row_is_settled_regardless_of_the_clock():
    # 'dead' means realized temperature already ruled it out -- hard evidence
    # beats the diurnal heuristic.
    assert screen_view.settled_of(_row_hrs(23.0, "low", kind="dead")) == "Yes"


NOW = datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc)


def test_new_tickers_are_the_ones_this_firing_added():
    rows = [_c("2026-08-04T11:00:00Z", "carried", 0.2, 5.0),
            _c("2026-08-04T12:00:00Z", "carried", 0.2, 5.0),
            _c("2026-08-04T12:00:00Z", "fresh", 0.3, 6.0)]
    assert screen_view.new_tickers(rows, NOW) == {"fresh"}


def test_everything_in_a_first_ever_firing_is_new():
    rows = [_c("2026-08-04T12:00:00Z", "a", 0.2, 5.0),
            _c("2026-08-04T12:00:00Z", "b", 0.3, 6.0)]
    assert screen_view.new_tickers(rows, NOW) == {"a", "b"}


def test_nothing_is_new_once_the_firing_is_over_an_hour_old():
    # The highlight means "arrived within the hour", so a stale log -- a missed
    # cron, a page left open -- must stop glowing rather than lie.
    rows = [_c("2026-08-04T10:00:00Z", "old", 0.2, 5.0)]
    assert screen_view.new_tickers(rows, NOW) == set()


def test_a_returning_bracket_counts_as_new_again():
    # Flagged at 10:00, gone at 11:00, back at 12:00: it is news again.
    rows = [_c("2026-08-04T10:00:00Z", "x", 0.2, 5.0),
            _c("2026-08-04T11:00:00Z", "other", 0.2, 5.0),
            _c("2026-08-04T12:00:00Z", "x", 0.3, 6.0)]
    assert screen_view.new_tickers(rows, NOW) == {"x"}


def test_table_marks_a_new_row_for_the_stylesheet():
    html = screen_view._table(["City"], [{"City": "Denver", "_class": "snew"}])
    assert '<tr class="snew">' in html
    assert "_class" not in html                  # the marker is not a column


def test_table_leaves_an_ordinary_row_unclassed():
    assert "<tr>" in screen_view._table(["City"], [{"City": "Denver"}])


def _mkt(ticker, no_ask=None, yes_bid=None):
    """A raw Kalshi market dict — prices are dollar STRINGS, as the API sends."""
    m = {"ticker": ticker}
    if no_ask is not None:
        m["no_ask_dollars"] = no_ask
    if yes_bid is not None:
        m["yes_bid_dollars"] = yes_bid
    return m


def test_no_ask_is_what_buying_no_actually_costs():
    assert screen_view.no_ask_of(_mkt("x", no_ask="0.8800")) == 0.88


def test_no_ask_falls_back_to_the_yes_bid_when_there_is_no_no_offer():
    # You sell against the resting YES bid: NO ask = 1 - yes bid.
    assert screen_view.no_ask_of(_mkt("x", yes_bid="0.1300")) == 0.87


def test_no_ask_of_an_unquoted_market_is_none():
    assert screen_view.no_ask_of(_mkt("x")) is None


def test_live_no_prices_fetches_one_ladder_per_series():
    rows = [_c("t", "KXLOWTDEN-A", 0.2, 5.0), _c("t", "KXLOWTDEN-B", 0.3, 6.0)]
    calls = []

    def fetch(series):
        calls.append(series)
        return [_mkt("KXLOWTDEN-A", no_ask="0.8000"),
                _mkt("KXLOWTDEN-B", yes_bid="0.2500"),
                _mkt("KXLOWTDEN-C", no_ask="0.9000")]   # not a candidate

    got = screen_view.live_no_prices(rows, fetch=fetch)
    assert calls == ["KXLOWTDEN"]                        # one call, not one/row
    assert got == {"KXLOWTDEN-A": 0.8, "KXLOWTDEN-B": 0.75}


def test_live_no_prices_survives_a_dead_series():
    # One city's ladder failing must cost that city's rows their live price,
    # not take the whole page down.
    dead = _c("t", "KXLOWTDEN-A", 0.2, 5.0)
    alive = dict(_c("t", "KXHIGHPHIL-A", 0.3, 6.0), series="KXHIGHPHIL")

    def fetch(series):
        if series == "KXLOWTDEN":
            raise RuntimeError("kalshi down")
        return [_mkt("KXHIGHPHIL-A", no_ask="0.6000")]

    got = screen_view.live_no_prices([dead, alive], fetch=fetch)
    assert got == {"KXHIGHPHIL-A": 0.6}


def test_live_price_renders_as_a_whole_percent():
    assert screen_view._pct(0.675) == "68%"
    assert screen_view._pct(None) == "—"


def test_screened_tickers_span_every_firing_not_just_the_newest():
    # A bracket bought this morning may not be in the latest firing -- its price
    # rose past the cap, or the gap closed. The position must not vanish from
    # the table exactly when it starts working.
    rows = [_c("2026-08-03T12:00:00Z", "old", 0.2, 5.0),
            _c("2026-08-03T18:00:00Z", "new", 0.3, 6.0)]
    assert set(screen_view.screened_by_ticker(rows)) == {"old", "new"}


def test_screened_tickers_keep_the_newest_row_for_a_repeated_bracket():
    rows = [_c("2026-08-03T12:00:00Z", "x", 0.2, 5.0),
            _c("2026-08-03T18:00:00Z", "x", 0.3, 9.0)]
    assert screen_view.screened_by_ticker(rows)["x"]["gap"] == 9.0


# ---- Earnings history table -------------------------------------------------

DEN = "KXLOWTDEN-26AUG04-B66.5"


def _trade(ticker=DEN, status="open", pnl=None, entry=0.78, now=0.84, qty=10.0,
           staked=7.80, label="66.5 to 67.5", exit_price=None, side="no"):
    """A row in the shape bet_history.build_rows emits."""
    return {"ticker": ticker, "label": label, "side": side, "entry": entry,
            "exit": exit_price, "qty": qty, "status": status, "pnl": pnl,
            "staked": staked, "current_value": now, "result": None,
            "first_ts": datetime(2026, 8, 4, 15, tzinfo=timezone.utc),
            "settled_ts": None}


def test_trade_row_marks_an_open_position_to_the_live_bid():
    row = screen_view.trade_display_rows([_trade()], {})[0]
    assert row["City"] == "Denver"                  # from the TICKER, not a flag
    assert row["Contract"] == "66.5 to 67.5"        # Kalshi's own wording
    assert (row["Entry"], row["Exit"]) == ("78¢", "84¢")
    assert row["P&L"] == "~+$0.60"                  # 10 x (0.84 - 0.78)
    assert row["% Gain"] == "~+7.7%"
    assert row["Result"] == "Open"
    assert row["_class"] == "sopen"                 # tinted: not a realized number


def test_trade_row_shows_a_settled_win_as_realized():
    row = screen_view.trade_display_rows(
        [_trade(status="settled", pnl=2.20, exit_price=1.0, now=None)], {})[0]
    assert (row["P&L"], row["Result"], row["_class"]) == ("+$2.20", "Won", "")
    assert row["Exit"] == "100¢"


def test_trade_row_reads_won_or_lost_not_the_brackets_own_outcome():
    # Nearly every trade here is a NO fade, so Kalshi's 'no' result would label a
    # winner with the word for the bracket losing.
    loss = screen_view.trade_display_rows(
        [_trade(status="settled", pnl=-7.80, exit_price=0.0, now=None)], {})[0]
    # Both the money and the percent use the app's true minus sign, so one table
    # does not spell a loss two ways.
    assert (loss["Result"], loss["P&L"], loss["% Gain"]) == (
        "Lost", "−$7.80", "−100.0%")


def test_trade_row_labels_a_position_you_sold_out_of():
    row = screen_view.trade_display_rows(
        [_trade(status="closed", pnl=0.40, exit_price=0.82, now=None)], {})[0]
    assert (row["Result"], row["P&L"]) == ("Sold", "+$0.40")


def test_trade_row_survives_an_open_position_with_no_mark():
    # market_price returns None on a market with no live quote; the row must
    # still render rather than the section dying.
    row = screen_view.trade_display_rows([_trade(now=None)], {})[0]
    assert (row["Exit"], row["P&L"], row["% Gain"]) == ("—", "—", "—")
    assert row["Result"] == "Open"


def test_trade_row_says_whether_the_screen_flagged_the_bracket():
    # Membership here is by city, so a bet the screen never listed can appear.
    screened = screen_view.screened_by_ticker([_c("t", DEN, 0.2, 5.0)])
    flagged = screen_view.trade_display_rows([_trade()], screened)[0]
    unflagged = screen_view.trade_display_rows([_trade()], {})[0]
    assert (flagged["Flagged"], unflagged["Flagged"]) == ("Yes", "—")


def test_contract_falls_back_to_the_candidate_when_meta_was_not_fetched():
    # build_rows defaults `label` to the ticker when no metadata was passed.
    cand = _c("t", DEN, 0.2, 5.0)
    row = screen_view.trade_display_rows([_trade(label=DEN)], {DEN: cand})[0]
    assert row["Contract"] == "72-73"               # floor/cap from the candidate


def test_contract_falls_back_to_the_strike_when_nothing_knows_the_label():
    row = screen_view.trade_display_rows([_trade(label=DEN)], {})[0]
    assert row["Contract"] == "B66.5"               # identifiable, at least


def test_city_of_an_unmapped_ticker_is_its_own_series():
    assert screen_view.city_of_ticker("KXHIGHNOWHERE-26AUG04-T90") == \
        "KXHIGHNOWHERE"


def test_every_trade_column_carries_a_tooltip_or_needs_none():
    # City and Contract are self-evident; the rest each state a convention
    # (fees, '~' marks, what Flagged's em dash means) that the table cannot.
    untipped = [c for c in screen_view._TRADE_COLUMNS
                if c not in screen_view._TRADE_TIPS]
    assert untipped == ["City", "Contract"]


def test_the_trade_table_does_not_inherit_the_candidate_tables_meanings():
    # 'Side' above is the side to BUY (always NO); 'Settled' above is whether the
    # day's extreme has formed. One shared tip map would explain the wrong thing.
    header = screen_view._table(["Side"], [{"Side": "NO"}],
                                screen_view._TRADE_TIPS)
    assert "side you actually hold" in header
    assert "Settled" not in screen_view._TRADE_COLUMNS


def test_empty_notice_says_whether_you_traded_anything_at_all():
    # The section used to render nothing in every failure mode, which is how a
    # feed that could not see 38 of the 40 cities went unnoticed.
    assert screen_view.empty_notice(0) == "No trades in screened brackets since Aug 3."
    assert screen_view.empty_notice(2) == (
        "No trades in screened brackets since Aug 3 — 2 other brackets traded "
        "(Dallas and Austin are on the History page).")


def test_earnings_caption_flags_the_live_point_and_the_unrealized_part():
    summary = screen_pnl.summary(
        [_trade(), _trade(ticker="KXHIGHDEN-26AUG03-T95", status="settled",
                          pnl=2.20, now=None)])
    caption = screen_view.earnings_caption(summary)
    assert "dashed stretch is live" in caption
    # Dollar signs are escaped: st.caption is markdown, and a PAIR of them renders
    # the text between as inline LaTeX (the caption became one italic equation).
    assert r"+\$0.60 unrealized" in caption
    assert r"\$15.60 staked" in caption          # staked is unsigned, not '+$'



def test_earnings_caption_says_when_nothing_is_realized_yet():
    caption = screen_view.earnings_caption(screen_pnl.summary([_trade()]))
    assert "Nothing has settled yet" in caption


def _pt(day, total, unrealized=0.0):
    return {"date": date(2026, 8, day), "total": total,
            "unrealized": unrealized, "open": unrealized != 0.0}


def test_line_parts_split_where_the_money_stops_being_banked():
    curve = [_pt(2, 0.0), _pt(3, 2.2), _pt(4, 3.5, 1.3), _pt(5, 4.0, 0.5)]
    realized, unrealized = screen_view.line_parts(curve)
    assert [p["date"].day for p in realized] == [2, 3]
    # The stretches share the Aug 3 point so the dashes continue the line rather
    # than starting after a gap.
    assert [p["date"].day for p in unrealized] == [3, 4, 5]


def test_line_parts_of_an_all_realized_curve_draw_nothing_dashed():
    curve = [_pt(2, 0.0), _pt(3, 2.2)]
    realized, unrealized = screen_view.line_parts(curve)
    assert len(realized) == 2
    assert len(unrealized) == 1              # one point draws no segment


def test_line_parts_treat_everything_after_a_mid_history_open_day_as_a_mark():
    # Once one day's step is a live mark, every cumulative total past it is one.
    curve = [_pt(2, 0.0), _pt(3, 1.0, 1.0), _pt(4, 3.0)]
    realized, unrealized = screen_view.line_parts(curve)
    assert [p["date"].day for p in realized] == [2, 3, 4]
    assert len(unrealized) == 1


def test_earnings_chart_draws_the_open_stretch_dashed_and_hollow():
    curve = [_pt(2, 0.0), _pt(3, 2.2), _pt(4, 3.5, 1.3)]
    spec = screen_view.earnings_chart(curve, "#51cf66").to_dict()
    dashes = [l["mark"].get("strokeDash") for l in spec["layer"]
              if l["mark"]["type"] == "line"]
    assert dashes == [None, [5, 4]]          # realized solid, then the mark
    dots = next(l for l in spec["layer"] if l["mark"]["type"] == "point")
    # Hollow on an open day: `fill` is encodable where mark_point's `filled` is not.
    assert dots["encoding"]["fill"]["condition"]["test"] == "datum.open"
    assert dots["encoding"]["fill"]["condition"]["value"] == "transparent"


def test_earnings_caption_says_the_dashed_part_is_not_banked():
    summary = screen_pnl.summary([_trade()])
    assert "dashed stretch is live, not banked" in \
        screen_view.earnings_caption(summary)


def test_earnings_chart_plots_dollars_against_the_weather_day():
    curve = screen_pnl.earnings_curve(
        [_trade(status="settled", pnl=2.20, now=None)], date(2026, 8, 5))
    spec = screen_view.earnings_chart(curve, "#51cf66").to_dict()
    line = spec["layer"][1]
    assert (line["encoding"]["y"]["field"], line["encoding"]["x"]["field"]) == (
        "total", "date")
    # The break-even rule sits at $0: this line starts from zero, not a bankroll.
    rule = spec["layer"][0]
    assert rule["mark"]["type"] == "rule"
    assert spec["datasets"][rule["data"]["name"]] == [{"y": 0.0}]
    # Dates are converted before plotting -- bare strings on a :T axis render a
    # day early for US viewers.
    days = [p["date"][:10] for p in
            spec["datasets"][spec["layer"][1]["data"]["name"]]]
    # The Aug 4 bracket's own weather day, led by the $0 anchor the day before.
    assert days == ["2026-08-03", "2026-08-04"]


def test_bracket_label_prefers_kalshis_wording():
    row = _c("t", "x", 0.4, 5.0)
    row.update(strike_type="greater", floor=90, cap=None,
               label="91° or above")
    assert screen_view._bracket_label(row) == "91° or above"


def test_bracket_label_falls_back_for_rows_logged_before_labels():
    row = _c("t", "x", 0.4, 5.0)
    row.update(strike_type="greater", floor=90, cap=None, label=None)
    assert screen_view._bracket_label(row) == ">90"


# ---- Live NO-price gate ----------------------------------------------------
# A NO ask of 9% means the live market has YES at ~91%: the bracket is already
# resolved and the screen's reference is what is wrong, not the price. The
# firing-time gates in screen_rules cannot see this -- their price is up to an
# hour stale.

def test_a_row_the_market_has_already_resolved_is_hidden():
    rows = [_c("t", "settled", 0.82, 5.0)]
    visible, cheap, dear = screen_view.tradeable_now(rows, {"settled": 0.09})
    assert visible == []
    assert (cheap, dear) == (1, 0)


def test_a_row_at_the_threshold_survives():
    # Strictly below hides: 20% is still a fade worth reviewing.
    rows = [_c("t", "edge", 0.80, 5.0)]
    visible, cheap, dear = screen_view.tradeable_now(
        rows, {"edge": screen_view.MIN_LIVE_NO_PRICE})
    assert [r["ticker"] for r in visible] == ["edge"]
    assert (cheap, dear) == (0, 0)


def test_an_ordinarily_priced_row_survives():
    rows = [_c("t", "live", 0.55, 5.0)]
    visible, cheap, dear = screen_view.tradeable_now(rows, {"live": 0.45})
    assert [r["ticker"] for r in visible] == ["live"]
    assert (cheap, dear) == (0, 0)


def test_a_row_with_no_live_quote_survives():
    # No quote is thin liquidity or a just-closed market -- not evidence the
    # market resolved against the fade. Hiding it would drop rows for the
    # wrong reason.
    rows = [_c("t", "unquoted", 0.60, 5.0)]
    visible, cheap, dear = screen_view.tradeable_now(rows, {})
    assert [r["ticker"] for r in visible] == ["unquoted"]
    assert (cheap, dear) == (0, 0)


def test_the_gate_counts_every_row_it_hides():
    rows = [_c("t", "settled", 0.82, 5.0), _c("t", "dead2", 0.95, 6.0),
            _c("t", "live", 0.55, 7.0), _c("t", "unquoted", 0.60, 8.0)]
    live = {"settled": 0.09, "dead2": 0.06, "live": 0.45}
    visible, cheap, dear = screen_view.tradeable_now(rows, live)
    assert [r["ticker"] for r in visible] == ["live", "unquoted"]
    assert (cheap, dear) == (2, 0)


def test_the_gate_preserves_row_order():
    rows = [_c("t", "a", 0.3, 5.0), _c("t", "gone", 0.9, 6.0),
            _c("t", "b", 0.4, 7.0)]
    visible, _, _ = screen_view.tradeable_now(rows, {"gone": 0.05})
    assert [r["ticker"] for r in visible] == ["a", "b"]


def test_a_hidden_row_is_not_counted_as_a_fresh_arrival():
    # The red highlight counts what arrived this hour; a row the gate removed
    # is not on screen and must not be counted red.
    rows = [_c("2026-08-04T12:00:00Z", "shown", 0.3, 5.0),
            _c("2026-08-04T12:00:00Z", "hidden", 0.9, 6.0)]
    visible, _, _ = screen_view.tradeable_now(rows, {"hidden": 0.04})
    fresh = screen_view.new_tickers(rows, NOW) & {r["ticker"] for r in visible}
    assert fresh == {"shown"}


def test_hidden_notice_explains_the_missing_rows():
    assert screen_view.hidden_notice(0, 0) == ""
    assert screen_view.hidden_notice(1, 0) == (
        "1 hidden — live NO under 20%, the market has already resolved it.")
    assert screen_view.hidden_notice(3, 0) == (
        "3 hidden — live NO under 20%, the market has already resolved them.")


def test_hidden_notice_names_both_reasons_separately():
    # They are opposite failures: under 20% the market says the fade is WRONG,
    # over 90% it agrees and there is nothing left to win. One combined count
    # would hide which is happening.
    text = screen_view.hidden_notice(2, 3)
    assert "2 hidden — live NO under 20%" in text
    assert "3 over 90%" in text


def test_hidden_notice_for_expensive_rows_alone():
    assert screen_view.hidden_notice(0, 1) == (
        "1 hidden — live NO over 90%: 10¢ of upside for 90¢ of risk.")


# ---- Freshness window tracks the firing cadence ----------------------------
# The screen fires every 30 min (external cron; the in-repo schedule is a
# best-effort fallback GitHub delivers ~62% of). The highlight window must
# cover one firing plus the scheduler's slack, and no more -- an hour-wide
# window on a 30-min cadence would keep claiming rows are new a firing after
# they stopped being.

def test_the_new_window_is_derived_from_the_firing_interval():
    # Not an independent magic number: a cadence change must move the window
    # with it, or the highlight quietly starts lying.
    assert screen_view.FIRING_INTERVAL == timedelta(minutes=30)
    assert screen_view.NEW_WINDOW == screen_view.FIRING_INTERVAL + timedelta(
        minutes=15)


def test_a_firing_one_full_interval_old_still_highlights():
    # The common case: you open the page just before the next firing lands.
    rows = [_c("2026-08-04T11:30:00Z", "carried", 0.2, 5.0),
            _c("2026-08-04T12:00:00Z", "carried", 0.2, 5.0),
            _c("2026-08-04T12:00:00Z", "fresh", 0.3, 6.0)]
    assert screen_view.new_tickers(rows, NOW) == {"fresh"}   # 30 min old


def test_a_skipped_firing_stops_the_highlight():
    # One missed firing (30 min) plus the scheduler's slack (15) is the limit:
    # past that the log is stale and nothing on it arrived recently.
    stale = NOW - timedelta(minutes=50)
    rows = [_c(stale.isoformat().replace("+00:00", "Z"), "old", 0.2, 5.0)]
    assert screen_view.new_tickers(rows, NOW) == set()


def test_a_firing_older_than_the_window_stops_glowing():
    # A missed cron or a page left open must stop claiming anything is fresh.
    late = NOW - screen_view.NEW_WINDOW - timedelta(minutes=1)
    rows = [_c(late.isoformat().replace("+00:00", "Z"), "old", 0.2, 5.0)]
    assert screen_view.new_tickers(rows, NOW) == set()


# ---- Storm column ----------------------------------------------------------

def test_storm_renders_as_a_whole_percent():
    assert screen_view.storm_of({"storm": 70}) == "70%"
    assert screen_view.storm_of({"storm": 0}) == "0%"


def test_a_row_logged_before_the_storm_field_reads_as_a_dash():
    # No migration: an old row and a closed window both mean "nothing to say".
    assert screen_view.storm_of({}) == "—"
    assert screen_view.storm_of({"storm": None}) == "—"


def test_the_storm_column_sits_next_to_the_gap_it_qualifies():
    # Gap, then the two things that qualify it: how strong it is for its lead,
    # and whether convection makes the reference itself untrustworthy.
    cols = screen_view._COLUMNS
    assert cols[cols.index("Gap") + 2] == "Storm"


# ---- Track record block ----------------------------------------------------

def test_track_record_caption_states_the_edge_against_the_price():
    summary = {"n": 40, "wins": 30, "hit_rate": 0.75, "mean_implied": 0.68,
               "edge": 0.07, "ev_per_contract": 0.07, "total_pnl": 2.80,
               "se": 0.0685, "enough": True, "exact_n": 40}
    text = screen_view.track_record_caption(summary, base=0.834)
    assert "40" in text and "75.0%" in text and "68.0%" in text
    assert "+7.0%" in text                      # the edge, signed
    assert "83.4%" in text                      # the base rate, always present


def test_a_thin_track_record_declines_a_verdict():
    summary = {"n": 3, "wins": 3, "hit_rate": 1.0, "mean_implied": 0.7,
               "edge": 0.3, "ev_per_contract": 0.3, "total_pnl": 0.9,
               "se": 0.0, "enough": False, "exact_n": 0}
    text = screen_view.track_record_caption(summary, base=0.834)
    assert "too thin" in text.lower() or "no verdict" in text.lower()
    assert "100" not in text.split("—")[0]      # no triumphant headline number


def test_no_settled_candidates_yet_says_so():
    empty = {"n": 0, "wins": 0, "hit_rate": None, "mean_implied": None,
             "edge": None, "ev_per_contract": None, "total_pnl": 0.0,
             "se": None, "enough": False, "exact_n": 0}
    assert "nothing has settled" in screen_view.track_record_caption(
        empty, base=None).lower()


# ---- Upper price gate ------------------------------------------------------
# The mirror of MIN_LIVE_NO_PRICE. Under 20% the market says the fade is wrong;
# over 90% it already agrees, and paying 90c to win 10c is not a trade worth
# taking however right the screen is.

def test_a_fade_with_almost_no_upside_left_is_hidden():
    rows = [_c("t", "expensive", 0.05, 5.0)]
    visible, low, high = screen_view.tradeable_now(rows, {"expensive": 0.93})
    assert visible == []
    assert (low, high) == (0, 1)


def test_a_fade_at_exactly_the_cap_survives():
    rows = [_c("t", "edge", 0.10, 5.0)]
    visible, low, high = screen_view.tradeable_now(
        rows, {"edge": screen_view.MAX_LIVE_NO_PRICE})
    assert [r["ticker"] for r in visible] == ["edge"]
    assert (low, high) == (0, 0)


def test_both_gates_count_separately():
    rows = [_c("t", "cheap", 0.9, 5.0), _c("t", "dear", 0.05, 6.0),
            _c("t", "good", 0.5, 7.0)]
    live = {"cheap": 0.08, "dear": 0.95, "good": 0.45}
    visible, low, high = screen_view.tradeable_now(rows, live)
    assert [r["ticker"] for r in visible] == ["good"]
    assert (low, high) == (1, 1)


# ---- Strength column -------------------------------------------------------

def test_strength_renders_as_a_multiple_of_the_bar():
    row = dict(_c("t", "x", 0.3, 6.0), variable="high", hours_to_close=3.0)
    assert screen_view.strength_of(row) == "1.5×"


def test_a_day_ahead_row_reads_below_the_bar():
    row = dict(_c("t", "x", 0.3, 6.0), variable="high", hours_to_close=36.0)
    assert screen_view.strength_of(row) == "0.6×"


def test_strength_is_a_dash_when_it_cannot_be_computed():
    assert screen_view.strength_of({"gap": None}) == "—"


def test_the_strength_column_sits_beside_the_gap_it_scales():
    cols = screen_view._COLUMNS
    assert cols[cols.index("Gap") + 1] == "Str"


def test_rows_are_still_ordered_by_urgency_not_strength():
    # Deliberate: a bracket three hours out needs a decision now, however weak,
    # and a strong one thirty hours out can wait for the next firing.
    rows = [dict(_c("t", "strong-far", 0.3, 12.0), hours_to_close=30.0),
            dict(_c("t", "weak-soon", 0.3, 4.0), hours_to_close=2.0)]
    assert [r["ticker"] for r in screen_view.display_rows(rows)] == [
        "weak-soon", "strong-far"]


# ---- The chart and the table must reconcile --------------------------------
# Reported 2026-08-05: the chart's Aug 3 point was $0.83 and Aug 4 was $0.72, a
# −$0.11 step, while summing the table's Aug 4 rows by eye gave +$0.05. Neither
# number was wrong; they were different quantities under similar labels. The
# table dated a row by the FILL (in UTC, at that), the chart by the day the
# market is about, so a bracket bought Aug 3 for the Aug 4 market appeared in one
# group and was counted in the other.

def _t(ticker, status, pnl=None, mark=None, entry=0.30, qty=10.0, bought=4,
       hour=15):
    return {"ticker": ticker, "label": "x", "side": "no", "entry": entry,
            "exit": None, "qty": qty, "status": status, "pnl": pnl,
            "staked": entry * qty, "current_value": mark, "result": None,
            "first_ts": datetime(2026, 8, bought, hour, tzinfo=timezone.utc),
            "settled_ts": None}


def test_each_days_table_rows_sum_to_that_days_step_on_the_chart():
    rows = [
        # Bought Aug 3, but it is the Aug 4 market: the row the two views
        # disagreed about. Under the old fill-dated column it read 'Aug 3'.
        _t("KXHIGHDEN-26AUG04-T95", "settled", pnl=-0.16, bought=3),
        _t("KXLOWTNYC-26AUG04-B72", "settled", pnl=0.05, bought=4),
        _t("KXHIGHPHIL-26AUG03-T90", "settled", pnl=0.83, bought=3),
    ]
    steps = {p["date"].strftime("%b %-d"): p["step"] for p
             in screen_view.with_steps(
                 screen_pnl.earnings_curve(rows, date(2026, 8, 5)))}
    # trade_display_rows preserves order, so each display row pairs with its trade.
    per_day = {}
    for trade, shown in zip(rows, screen_view.trade_display_rows(rows, {})):
        per_day[shown["Day"]] = per_day.get(shown["Day"], 0.0) + trade["pnl"]

    assert steps["Aug 4"] == pytest.approx(-0.11)      # what the chart shows
    assert per_day["Aug 4"] == pytest.approx(-0.11)    # what the table adds to
    assert steps["Aug 3"] == per_day["Aug 3"] == pytest.approx(0.83)


def test_the_readout_states_what_a_day_added_not_just_the_running_total():
    # Reading a step off the line meant subtracting two points by eye.
    # The $0 anchor leads the curve, and has no prior day to step from.
    curve = [{"date": date(2026, 8, 2), "total": 0.0, "unrealized": 0.0,
              "open": False},
             {"date": date(2026, 8, 3), "total": 0.83, "unrealized": 0.0,
              "open": False},
             {"date": date(2026, 8, 4), "total": 0.72, "unrealized": 0.0,
              "open": False}]
    assert [p["step"] for p in screen_view.with_steps(curve)] == [
        0.0, pytest.approx(0.83), pytest.approx(-0.11)]


def test_the_day_column_is_the_market_day_not_the_fill_date():
    row = screen_view.trade_display_rows(
        [_t("KXHIGHDEN-26AUG04-T95", "settled", pnl=-0.16, bought=3)], {})[0]
    assert row["Day"] == "Aug 4"


def test_an_evening_fill_is_not_dated_a_day_late():
    # 01:00Z on Aug 4 is 8pm CDT Aug 3 — an evening firing's trade. Dating rows
    # by the market day makes the UTC roll-over irrelevant.
    row = screen_view.trade_display_rows(
        [_t("KXLOWTDEN-26AUG04-B66.5", "open", mark=0.35, bought=4, hour=1)],
        {})[0]
    assert row["Day"] == "Aug 4"


# ---- Days must be contiguous, and add up on the page -----------------------
# Second report, 2026-08-05: dating rows by market day was not enough. The rows
# were still ORDERED by fill time, so a bracket bought Aug 3 for the Aug 4 market
# sorted below the Aug 5 rows — the Aug 4 group was split, and reading it as a
# contiguous block missed the stranded row entirely.

def test_rows_are_grouped_by_the_day_they_are_filed_under():
    rows = [_t("KXLOWTNYC-26AUG05-B72", "settled", pnl=0.20, bought=4, hour=18),
            _t("KXHIGHTMIN-26AUG04-T88", "settled", pnl=0.05, bought=4, hour=10),
            _t("KXHIGHPHIL-26AUG03-T90", "settled", pnl=0.83, bought=3, hour=20),
            # bought EARLIEST, but it is the Aug 4 market: it belongs with Aug 4
            _t("KXHIGHDEN-26AUG04-T95", "settled", pnl=-0.16, bought=3, hour=9)]
    days = [r["Day"] for r in screen_view.table_rows(rows, {})
            if not r.get("_class") == "ssub"]
    assert days == ["Aug 5", "Aug 4", "Aug 4", "Aug 3"]


def test_each_day_carries_a_subtotal_matching_the_charts_step():
    rows = [_t("KXHIGHTMIN-26AUG04-T88", "settled", pnl=0.05, bought=4),
            _t("KXHIGHDEN-26AUG04-T95", "settled", pnl=-0.16, bought=3),
            _t("KXHIGHPHIL-26AUG03-T90", "settled", pnl=0.83, bought=3)]
    subtotals = {r["Day"]: r["P&L"] for r in screen_view.table_rows(rows, {})
                 if r.get("_class") == "ssub"}
    assert subtotals["Aug 4 total"] == "−$0.11"     # the step the chart draws
    assert subtotals["Aug 3 total"] == "+$0.83"


def test_a_subtotal_counts_open_marks_and_says_so():
    rows = [_t("KXHIGHTMIN-26AUG04-T88", "settled", pnl=0.05, bought=4),
            _t("KXHIGHDEN-26AUG04-T95", "open", mark=0.14, entry=0.30, qty=10.0,
               bought=3)]
    sub = next(r for r in screen_view.table_rows(rows, {})
               if r.get("_class") == "ssub")
    # 0.05 + 10*(0.14-0.30) = -1.55, and the '~' says part of it is a mark.
    assert sub["P&L"] == "~−$1.55"
    assert sub["Result"] == "2 trades, 1 open"


def test_a_subtotal_stands_alone_when_a_day_has_one_trade():
    rows = [_t("KXHIGHPHIL-26AUG03-T90", "settled", pnl=0.83, bought=3)]
    sub = next(r for r in screen_view.table_rows(rows, {})
               if r.get("_class") == "ssub")
    assert (sub["P&L"], sub["Result"]) == ("+$0.83", "1 trade")


def test_a_days_subtotals_reconcile_with_every_step_on_the_line():
    # The invariant, now checked on what the page actually renders.
    rows = [_t("KXLOWTNYC-26AUG05-B72", "open", mark=0.44, entry=0.31, bought=4),
            _t("KXHIGHTMIN-26AUG04-T88", "settled", pnl=0.05, bought=4),
            _t("KXHIGHDEN-26AUG04-T95", "settled", pnl=-0.16, bought=3),
            _t("KXHIGHPHIL-26AUG03-T90", "settled", pnl=0.83, bought=3)]
    steps = {p["date"].strftime("%b %-d") + " total": _money(p["step"])
             for p in screen_view.with_steps(
                 screen_pnl.earnings_curve(rows, date(2026, 8, 6)))
             if p["step"]}
    shown = {r["Day"]: r["P&L"].lstrip("~") for r in
             screen_view.table_rows(rows, {}) if r.get("_class") == "ssub"}
    assert shown == steps


def _money(v):
    return f"+${v:,.2f}" if v >= 0 else f"−${abs(v):,.2f}"


# ---- On-page reconciliation -------------------------------------------------

def test_reconciliation_shows_gross_fees_and_net_per_trade():
    rows = [dict(_t("KXHIGHTMIN-26AUG04-T88", "settled", pnl=2.16), fee=0.16)]
    days = screen_pnl.day_breakdown(rows, date(2026, 8, 5))
    row = screen_view.reconciliation_rows(days)[0]
    # +$2.32 of price move, 16c to Kalshi, +$2.16 in your account.
    assert (row["Gross"], row["Fees"], row["Net"]) == ("+$2.32", "−$0.16",
                                                       "+$2.16")


def test_reconciliation_subtotal_sums_each_column_and_checks_the_chart():
    rows = [dict(_t("KXHIGHTMIN-26AUG04-T88", "settled", pnl=2.16), fee=0.16),
            dict(_t("KXHIGHTDC-26AUG04-T96", "settled", pnl=-0.16), fee=0.09)]
    days = screen_pnl.day_breakdown(rows, date(2026, 8, 5))
    sub = next(r for r in screen_view.reconciliation_rows(days)
               if r.get("_class") == "ssub")
    assert sub["Gross"] == "+$2.25"          # 2.32 + (-0.07)
    assert sub["Fees"] == "−$0.25"
    assert sub["Net"] == "+$2.00"
    assert sub["Bracket"] == "chart step +$2.00 ✓"


def test_reconciliation_names_a_day_the_chart_cannot_place():
    rows = [dict(_t("NOT-A-DATE", "settled", pnl=0.42))]
    sub = next(r for r in screen_view.reconciliation_rows(
        screen_pnl.day_breakdown(rows, date(2026, 8, 5)))
        if r.get("_class") == "ssub")
    assert "not on the chart" in sub["Bracket"]


def test_reconciliation_marks_an_open_trade_as_open():
    rows = [_t("KXLOWTNYC-26AUG05-B72", "open", mark=0.44, entry=0.31)]
    row = screen_view.reconciliation_rows(
        screen_pnl.day_breakdown(rows, date(2026, 8, 6)))[0]
    assert row["Net"].endswith("(open)")


def test_the_drift_cell_shows_the_reference_and_where_it_lands():
    assert screen_view.drift_of({"forecast": 75.0, "drift_ref": 72.4}) == "75→72"


def test_the_drift_cell_rounds_half_up_to_the_settlement_basis():
    # Kalshi settles on whole degrees F, so the arrow speaks in them. Python's
    # own "%.0f" is half-EVEN and would render 72.5 as 72.
    assert screen_view.drift_of({"forecast": 71.6, "drift_ref": 71.6}) == "72→72"
    assert screen_view.drift_of({"forecast": 72.5, "drift_ref": 72.5}) == "73→73"


def test_a_row_without_drift_reads_as_a_dash():
    # Rows logged before this feature existed, and dead rows, both land here.
    assert screen_view.drift_of({"forecast": 75.0}) == "—"
    assert screen_view.drift_of({"forecast": 75.0, "drift_ref": None}) == "—"
    assert screen_view.drift_of({}) == "—"


def test_the_drift_column_sits_immediately_before_the_reference():
    assert "Drift" in screen_view._COLUMNS
    assert screen_view._COLUMNS.index("Drift") == screen_view._COLUMNS.index("Ref") - 1


def test_every_candidate_column_has_a_cell():
    row = screen_view._candidate_row({"series": "KXLOWTDEN", "variable": "low",
                             "ticker": "T", "label": "72° to 73°",
                             "floor": 72, "cap": 73, "strike_type": "between",
                             "forecast": 75.0, "drift_ref": 72.4, "gap": 4.0,
                             "hours_to_close": 11.0, "price": 0.35},
                            {}, set())
    for column in screen_view._COLUMNS:
        assert column in row


# ---- Which day a bracket settles on ----------------------------------------
#
# The alert loop only ever pushes brackets settling on the climate day running
# RIGHT NOW (screen_alert.check). The page used to redden any bracket the latest
# firing added, whichever day it settled on, so a red row promised a push it
# could not deliver: measured over 2026-08-08/09, 69 of the 84 newly-red rows
# were TOMORROW's markets and none of them could ever have alerted.

_ZONES = {"KXLOWTDEN": "America/Denver"}
# 15:00Z on Aug 7 is 08:00 MST, so Denver's climate day in progress is Aug 7.
_MIDDAY = datetime(2026, 8, 7, 15, 0, tzinfo=timezone.utc)


def _d(ticker, series="KXLOWTDEN"):
    return {"ts": "2026-08-07T15:00:00Z", "series": series, "ticker": ticker}


def test_a_bracket_settling_on_the_running_climate_day_is_todays():
    assert screen_view.settles_today(_d("KXLOWTDEN-26AUG07-B62.5"),
                                     _ZONES, _MIDDAY) is True


def test_tomorrows_bracket_is_not_todays_however_close_it_closes():
    assert screen_view.settles_today(_d("KXLOWTDEN-26AUG08-B62.5"),
                                     _ZONES, _MIDDAY) is False


def test_a_city_with_no_known_timezone_is_never_todays():
    # The alert skips a city the reference has no timezone for, so the page must
    # not claim a row it cannot have pushed.
    assert screen_view.settles_today(_d("KXLOWTZZZ-26AUG07-B62.5", "KXLOWTZZZ"),
                                     _ZONES, _MIDDAY) is False


def test_the_day_is_read_in_fixed_standard_time_not_local_time():
    # 05:30Z on Aug 8 is 23:30 MST on Aug 7: the climate day still running is
    # Aug 7, even though the local calendar has not been Aug 7 for hours in
    # daylight time.
    late = datetime(2026, 8, 8, 5, 30, tzinfo=timezone.utc)
    assert screen_view.settles_today(_d("KXLOWTDEN-26AUG07-B62.5"),
                                     _ZONES, late) is True


def test_the_day_column_names_which_day_the_bracket_is_about():
    assert screen_view.day_of(_d("KXLOWTDEN-26AUG07-B62.5"),
                              _ZONES, _MIDDAY) == "Today"
    assert screen_view.day_of(_d("KXLOWTDEN-26AUG08-B62.5"),
                              _ZONES, _MIDDAY) == "Tomorrow"


def test_a_day_we_cannot_place_reads_as_a_dash():
    assert screen_view.day_of(_d("nonsense"), _ZONES, _MIDDAY) == "—"
    assert screen_view.day_of(_d("KXLOWTZZZ-26AUG07-B62.5", "KXLOWTZZZ"),
                              _ZONES, _MIDDAY) == "—"


def test_only_todays_new_brackets_are_reddened():
    # Red means "your phone has this too". Tomorrow's bracket is new and stays
    # plain, because screen_alert never looks at it.
    rows = [_d("KXLOWTDEN-26AUG07-B62.5"), _d("KXLOWTDEN-26AUG08-B62.5")]
    assert screen_view.pushed_tickers(rows, _ZONES, _MIDDAY) == {
        "KXLOWTDEN-26AUG07-B62.5"}


def test_the_day_column_sits_where_a_phone_can_see_it():
    # Second column: at 390px only the first few are visible without scrolling,
    # and "is this today's?" is the question the red highlight now answers.
    assert screen_view._COLUMNS[1] == "Day"


def test_the_day_column_explains_what_red_promises():
    tip = screen_view._TIPS["Day"]
    assert "Tomorrow" in tip and "alert" in tip.lower()
