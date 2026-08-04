from datetime import datetime, timedelta, timezone

import pytest

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


def _pos(ticker, side="no", qty=10.0, entry=0.78, now=0.84):
    return {"ticker": ticker, "side": side, "qty": qty, "entry": entry,
            "current_value": now, "label": "from meta", "status": "open"}


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


def test_open_screened_keeps_only_positions_the_screen_flagged():
    screened = screen_view.screened_by_ticker([_c("t", "flagged", 0.2, 5.0)])
    got = screen_view.open_screened([_pos("flagged"), _pos("elsewhere")], screened)
    assert [p["ticker"] for p in got] == ["flagged"]


def test_position_row_shows_entry_against_the_live_mark():
    screened = screen_view.screened_by_ticker([_c("t", "x", 0.2, 5.0)])
    row = screen_view.position_rows([_pos("x")], screened)[0]
    assert row["City"] == "Denver"
    assert row["Contract"] == "72-73"          # from the candidate, not meta
    assert (row["Side"], row["Entry"], row["Now"]) == ("NO", "78¢", "84¢")
    assert row["Unreal P&L"] == "+$0.60"       # 10 x (0.84 - 0.78)


def test_position_row_shows_a_loss_signed():
    screened = screen_view.screened_by_ticker([_c("t", "x", 0.2, 5.0)])
    rows = screen_view.position_rows([_pos("x", entry=0.80, now=0.65)], screened)
    assert rows[0]["Unreal P&L"] == "−$1.50"


def test_position_row_survives_an_unpriced_market():
    # market_price returns None on a market with no live quote; the row must
    # still render rather than the section dying.
    screened = screen_view.screened_by_ticker([_c("t", "x", 0.2, 5.0)])
    row = screen_view.position_rows([_pos("x", now=None)], screened)[0]
    assert (row["Now"], row["Unreal P&L"]) == ("—", "—")


def _fill(ticker, action="buy", side="no", count=10.0, price=0.78):
    return {"trade_id": ticker + action, "ticker": ticker, "variable": None,
            "side": side, "action": action, "count": count, "price": price,
            "yes_price": 1 - price, "no_price": price, "fee": 0.0,
            "ts": datetime(2026, 8, 3, 15, tzinfo=timezone.utc)}


def test_build_positions_covers_a_city_the_model_does_not_run():
    # The whole point of the fix: KXLOWTDEN has no station in this app, so it
    # carries no `variable` -- it must still produce a position row.
    got = screen_view.build_positions([_fill("KXLOWTDEN-26AUG04-B66.5")], {},
                                      lambda t, side: 0.84)
    assert [(p["ticker"], p["side"], p["qty"], p["entry"], p["current_value"])
            for p in got] == [("KXLOWTDEN-26AUG04-B66.5", "no", 10.0, 0.78, 0.84)]


def test_build_positions_drops_a_settled_bracket():
    fills = [_fill("KXLOWTDEN-26AUG04-B66.5")]
    settled = {"KXLOWTDEN-26AUG04-B66.5": {
        "result": "no", "ts": datetime(2026, 8, 5, 6, tzinfo=timezone.utc),
        "revenue": 10.0, "fee": 0.0}}
    assert screen_view.build_positions(fills, settled, lambda t, s: 0.9) == []


def test_empty_notice_says_whether_you_hold_anything_at_all():
    # The section used to render nothing in every failure mode, which is how a
    # feed that could not see 38 of the 40 cities went unnoticed.
    assert screen_view.empty_notice([]) == "No open positions."
    assert screen_view.empty_notice([_pos("a"), _pos("b")]) == (
        "No open positions in a flagged bracket (2 open elsewhere).")


def test_total_unrealized_skips_unpriced_positions():
    positions = [_pos("a"), _pos("b", now=None)]
    assert screen_view.total_unrealized(positions) == pytest.approx(0.6)


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
    visible, hidden = screen_view.tradeable_now(rows, {"settled": 0.09})
    assert visible == []
    assert hidden == 1


def test_a_row_at_the_threshold_survives():
    # Strictly below hides: 20% is still a fade worth reviewing.
    rows = [_c("t", "edge", 0.80, 5.0)]
    visible, hidden = screen_view.tradeable_now(
        rows, {"edge": screen_view.MIN_LIVE_NO_PRICE})
    assert [r["ticker"] for r in visible] == ["edge"]
    assert hidden == 0


def test_an_ordinarily_priced_row_survives():
    rows = [_c("t", "live", 0.55, 5.0)]
    visible, hidden = screen_view.tradeable_now(rows, {"live": 0.45})
    assert [r["ticker"] for r in visible] == ["live"]
    assert hidden == 0


def test_a_row_with_no_live_quote_survives():
    # No quote is thin liquidity or a just-closed market -- not evidence the
    # market resolved against the fade. Hiding it would drop rows for the
    # wrong reason.
    rows = [_c("t", "unquoted", 0.60, 5.0)]
    visible, hidden = screen_view.tradeable_now(rows, {})
    assert [r["ticker"] for r in visible] == ["unquoted"]
    assert hidden == 0


def test_the_gate_counts_every_row_it_hides():
    rows = [_c("t", "settled", 0.82, 5.0), _c("t", "dead2", 0.95, 6.0),
            _c("t", "live", 0.55, 7.0), _c("t", "unquoted", 0.60, 8.0)]
    live = {"settled": 0.09, "dead2": 0.06, "live": 0.45}
    visible, hidden = screen_view.tradeable_now(rows, live)
    assert [r["ticker"] for r in visible] == ["live", "unquoted"]
    assert hidden == 2


def test_the_gate_preserves_row_order():
    rows = [_c("t", "a", 0.3, 5.0), _c("t", "gone", 0.9, 6.0),
            _c("t", "b", 0.4, 7.0)]
    visible, _ = screen_view.tradeable_now(rows, {"gone": 0.05})
    assert [r["ticker"] for r in visible] == ["a", "b"]


def test_a_hidden_row_is_not_counted_as_a_fresh_arrival():
    # The red highlight counts what arrived this hour; a row the gate removed
    # is not on screen and must not be counted red.
    rows = [_c("2026-08-04T12:00:00Z", "shown", 0.3, 5.0),
            _c("2026-08-04T12:00:00Z", "hidden", 0.9, 6.0)]
    visible, _ = screen_view.tradeable_now(rows, {"hidden": 0.04})
    fresh = screen_view.new_tickers(rows, NOW) & {r["ticker"] for r in visible}
    assert fresh == {"shown"}


def test_hidden_notice_explains_the_missing_rows():
    assert screen_view.hidden_notice(0) == ""
    assert screen_view.hidden_notice(1) == (
        "1 hidden — live NO under 20%, the market has already resolved it.")
    assert screen_view.hidden_notice(3) == (
        "3 hidden — live NO under 20%, the market has already resolved them.")


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
    cols = screen_view._COLUMNS
    assert cols[cols.index("Gap") + 1] == "Storm"
