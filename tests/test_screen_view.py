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
