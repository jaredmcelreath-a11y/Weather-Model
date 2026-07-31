import trade_view


def test_summarize_log_newest_first_and_capped():
    recs = [{"ts": f"2026-07-24T10:0{i}:00+00:00", "kind": "entry",
             "ticker": f"T{i}", "reason": ""} for i in range(5)]
    out = trade_view.summarize_log(recs, limit=3)
    assert len(out) == 3
    assert out[0]["ticker"] == "T4"       # newest first


def test_summarize_log_handles_missing_fields():
    out = trade_view.summarize_log([{"ts": "2026-07-24T10:00:00+00:00"}])
    assert out[0]["kind"] == "" and out[0]["ticker"] == ""


def test_safety_rows_reads_both_stations(monkeypatch):
    import config
    import trade_state

    def fake_load(transport=None, station=config.DEFAULT_STATION):
        return {"kill_switch": station == "KDFW", "mode": "shadow"}
    monkeypatch.setattr(trade_state, "load_state", fake_load)
    rows = trade_view.safety_rows()
    by = {r["station"]: r for r in rows}
    assert by["KDFW"]["kill_switch"] is True and by["KDFW"]["name"] == "Dallas"
    assert by["KAUS"]["kill_switch"] is False and by["KAUS"]["name"] == "Austin"


# --- Open Positions must show SHADOW holdings too ---------------------------
# In shadow mode no real order is ever placed, so the Kalshi account is empty
# while the trader is genuinely holding and managing a position out of
# trade_runtime.<station>.json (stop-loss, reversal exit). Reading only the
# account made the panel print "No Open Positions" under a logged entry.

_RUNTIME = {"entries": {"KXHIGHAUS-26JUL28-B97.5": {
    "entry_ask": 0.57, "side": "yes", "count": 1, "variable": "high"}}}


def test_position_rows_shadow_reads_the_runtime_record():
    rows = trade_view.position_rows("shadow", [], _RUNTIME)
    assert len(rows) == 1
    assert rows[0]["Contract"] == "97-98"
    assert rows[0]["Side"] == "Yes"
    assert rows[0]["Count"] == 1
    assert rows[0]["Entry"] == "0.57"


def test_position_rows_live_reads_the_account_enriched_with_entry():
    truth = [{"ticker": "KXHIGHAUS-26JUL28-B97.5", "side": "yes", "count": 2,
              "variable": "high"}]
    rows = trade_view.position_rows("live", truth, _RUNTIME)
    assert len(rows) == 1 and rows[0]["Count"] == 2      # the account is the truth
    assert rows[0]["Entry"] == "0.57"


def test_position_rows_live_ignores_the_shadow_record():
    # Nothing actually held -> nothing shown, even with a stale runtime entry.
    assert trade_view.position_rows("live", [], _RUNTIME) == []


def test_position_rows_tolerates_a_missing_entry_ask():
    rows = trade_view.position_rows("live", [{"ticker": "X", "side": "no",
                                              "count": 1, "variable": "low"}], {})
    assert rows[0]["Entry"] == "—"


# --- Task 4: full position columns ------------------------------------------

_RT2 = {"entries": {"KXHIGHAUS-26JUL28-B99.5": {
    "entry_ask": 0.70, "side": "yes", "count": 1, "variable": "high",
    "day": "2026-07-28", "floor": 99, "cap": 100, "label": "99° to 100°",
    "ts": "2026-07-28T16:12:30-05:00"}}}
_MARKS = {"KXHIGHAUS-26JUL28-B99.5": {"bid": 0.62, "ask": 0.65, "model": 0.43}}
_PARAMS = {"stop_loss": 0.20}
_COLS = ["Date", "Time", "Variable", "Contract", "Side", "Count", "Entry",
         "Current", "P&L", "Stop-out", "Model %"]


def test_position_rows_has_the_full_column_set_in_order():
    row = trade_view.position_rows("shadow", [], _RT2, _MARKS, _PARAMS)[0]
    assert list(row.keys()) == _COLS


def test_position_rows_date_and_time_come_from_the_entry_ts():
    row = trade_view.position_rows("shadow", [], _RT2, _MARKS, _PARAMS)[0]
    assert row["Date"] == "07-28"
    assert row["Time"] == "4:12 PM"


def test_position_rows_current_is_the_ask_and_pnl_uses_the_bid():
    # The stop-loss triggers on the ASK, but a sale fills into the BID. Showing
    # one ambiguous "price" would hide which number drives the stop.
    row = trade_view.position_rows("shadow", [], _RT2, _MARKS, _PARAMS)[0]
    assert row["Current"] == "0.65"
    assert row["P&L"] == "-0.08"            # (0.62 - 0.70) * 1


def test_position_rows_pnl_scales_with_count():
    rt = {"entries": {"T": {"entry_ask": 0.50, "side": "yes", "count": 3,
                            "variable": "high", "label": "x"}}}
    row = trade_view.position_rows("shadow", [], rt,
                                   {"T": {"bid": 0.60, "ask": 0.62}}, _PARAMS)[0]
    assert row["P&L"] == "+0.30"


def test_position_rows_stop_out_is_entry_minus_stop_loss():
    row = trade_view.position_rows("shadow", [], _RT2, _MARKS, _PARAMS)[0]
    assert row["Stop-out"] == "0.50"


def test_position_rows_model_percent_from_the_mark():
    row = trade_view.position_rows("shadow", [], _RT2, _MARKS, _PARAMS)[0]
    assert row["Model %"] == "43%"


def test_position_rows_without_marks_renders_dashes():
    row = trade_view.position_rows("shadow", [], _RT2)[0]
    assert row["Current"] == "—" and row["P&L"] == "—" and row["Model %"] == "—"
    assert row["Stop-out"] == "—"           # no params -> no stop to compute
    assert row["Entry"] == "0.70"           # still known


def test_position_rows_pre_schema_falls_back_to_the_ticker():
    rt = {"entries": {"KXHIGHAUS-26JUL28-B99.5": {
        "entry_ask": 0.70, "side": "yes", "count": 1, "variable": "high"}}}
    row = trade_view.position_rows("shadow", [], rt, {}, _PARAMS)[0]
    assert row["Date"] == "07-28"           # recovered from the ticker suffix
    assert row["Contract"] == "99-100"      # range derived from the B<mid> suffix
    assert row["Time"] == "—"


def test_open_marks_for_curve_shape():
    # The rows trade_pnl.unrealized consumes, built from the same marks.
    out = trade_view.open_marks_for_curve(_RT2, _MARKS)
    assert out == [{"ticker": "KXHIGHAUS-26JUL28-B99.5", "entry_ask": 0.70,
                    "count": 1, "bid": 0.62}]


# --- Task 5: split actions from the repetitive skips -------------------------

_LOG = [
    {"ts": "2026-07-28T21:02:19+00:00", "kind": "skip", "variable": "high",
     "reason": "model 96.4 vs market 98.96 disagree > 1.0°F"},
    {"ts": "2026-07-28T21:07:23+00:00", "kind": "entry", "variable": "high",
     "ticker": "KXHIGHAUS-26JUL28-B97.5", "side": "yes", "count": 1,
     "entry_ask": 0.57},
    {"ts": "2026-07-28T21:07:25+00:00", "kind": "skip", "variable": "low",
     "ticker": "KXLOWTAUS-26JUL28-B75.5", "reason": "market already settled"},
    {"ts": "2026-07-28T21:12:02+00:00", "kind": "exit", "variable": "high",
     "ticker": "KXHIGHAUS-26JUL28-B97.5",
     "reason": "reversal: target moved to KXHIGHAUS-26JUL28-B99.5",
     "exit_price": 0.55, "pnl": -0.02},
    {"ts": "2026-07-28T21:12:05+00:00", "kind": "entry", "variable": "high",
     "ticker": "KXHIGHAUS-26JUL28-B99.5", "side": "yes", "count": 1,
     "entry_ask": 0.70},
]


def test_partition_decisions_splits_actions_from_skips():
    actions, skips = trade_view.partition_decisions(_LOG)
    assert [a["kind"] for a in actions] == ["entry", "exit", "entry"]
    assert len(skips) == 2


def test_partition_decisions_is_newest_first():
    actions, skips = trade_view.partition_decisions(_LOG)
    assert actions[0]["ticker"] == "KXHIGHAUS-26JUL28-B99.5"
    assert skips[0]["variable"] == "low"


def test_partition_decisions_empty():
    assert trade_view.partition_decisions([]) == ([], [])


def test_action_rows_render_price_and_pnl():
    actions, _ = trade_view.partition_decisions(_LOG)
    rows = trade_view.action_rows(actions)
    assert list(rows[0].keys()) == ["Time", "Kind", "Contract", "Detail"]
    assert rows[0]["Kind"] == "Entry" and "0.70" in rows[0]["Detail"]
    assert rows[1]["Kind"] == "Exit" and "-0.02" in rows[1]["Detail"]


def test_status_strip_reports_latest_state_per_variable():
    out = trade_view.status_strip(_LOG, ["high", "low"])
    assert out["high"].startswith("holding")
    assert "99-100" in out["high"]
    assert "settled" in out["low"]


def test_status_strip_after_an_exit_is_flat():
    log = [_LOG[1], {"ts": "2026-07-28T22:00:00+00:00", "kind": "exit",
                     "variable": "high", "ticker": "KXHIGHAUS-26JUL28-B97.5",
                     "reason": "settled won (high 98)"}]
    assert "no position" in trade_view.status_strip(log, ["high"])["high"]


def test_status_strip_unknown_variable_says_so():
    assert trade_view.status_strip([], ["high"])["high"] == "no activity yet"


# --- Task 6: P&L chart frame -------------------------------------------------

def test_pnl_frame_labels_each_city_and_parses_dates():
    from datetime import date as _d
    df = trade_view.pnl_frame({
        "Dallas": [{"date": _d(2026, 7, 27), "total": 0.0},
                   {"date": _d(2026, 7, 28), "total": -0.25}],
        "Austin": [{"date": _d(2026, 7, 28), "total": 0.40}],
    })
    assert len(df) == 3
    assert set(df["city"]) == {"Dallas", "Austin"}
    # Bare date strings on an Altair :T axis render a day early — must be datetime.
    assert str(df["date"].dtype).startswith("datetime64")


def test_pnl_frame_empty_is_empty():
    assert trade_view.pnl_frame({}).empty
    assert trade_view.pnl_frame({"Dallas": []}).empty


# --- Record + realized-P&L boxes ---------------------------------------------

def test_record_card_shows_wins_dash_losses_and_the_rate():
    import trade_pnl
    s = trade_pnl.record_summary([{"pnl": 0.30}, {"pnl": 0.07}, {"pnl": -0.20}])
    val, help_text = trade_view.record_card_text(s)
    assert val == "2–1"
    assert "67%" in help_text


def test_record_card_names_the_trade_count():
    import trade_pnl
    s = trade_pnl.record_summary([{"pnl": 0.30}, {"pnl": -0.20}])
    _val, help_text = trade_view.record_card_text(s)
    assert "2 closed round trips" in help_text


def test_record_card_leaves_the_dollars_to_the_pnl_box():
    # The two boxes must never state the same number twice; realized lives in
    # its own card now.
    import trade_pnl
    s = trade_pnl.record_summary([{"pnl": 0.30}, {"pnl": 0.07}, {"pnl": -0.20}])
    assert "+0.17" not in trade_view.record_card_text(s)[1]


def test_record_card_with_no_closed_trade_is_a_dash():
    import trade_pnl
    val, help_text = trade_view.record_card_text(trade_pnl.record_summary([]))
    assert val == trade_view.DASH and "yet" in help_text


def test_record_card_names_break_evens_without_counting_them():
    import trade_pnl
    s = trade_pnl.record_summary([{"pnl": 0.30}, {"pnl": 0.0}])
    val, help_text = trade_view.record_card_text(s)
    assert val == "1–0" and "break-even" in help_text


def test_record_card_can_be_scoped_to_a_city():
    import trade_pnl
    s = trade_pnl.record_summary([{"pnl": 0.30}])
    assert "Dallas" in trade_view.record_card_text(s, scope="Dallas")[1]


def test_pnl_card_shows_signed_realized_dollars():
    import trade_pnl
    s = trade_pnl.record_summary([{"pnl": 0.56}, {"pnl": 0.14}, {"pnl": -0.20}])
    val, help_text = trade_view.pnl_card_text(s, unreal=None, open_count=0)
    assert val == "+0.50"
    assert "3 closed round trips" in help_text


def test_pnl_card_shows_a_loss_with_its_minus_sign():
    import trade_pnl
    s = trade_pnl.record_summary([{"pnl": -0.69}])
    assert trade_view.pnl_card_text(s, None, 0)[0] == "-0.69"


def test_pnl_card_with_no_closed_trade_is_a_dash_not_zero():
    # "+0.00" would claim a break-even result that never happened.
    import trade_pnl
    val, help_text = trade_view.pnl_card_text(trade_pnl.record_summary([]), None, 0)
    assert val == trade_view.DASH and "yet" in help_text


def test_pnl_card_names_open_positions_without_counting_them():
    import trade_pnl
    s = trade_pnl.record_summary([{"pnl": 0.30}])
    _val, help_text = trade_view.pnl_card_text(s, unreal=0.10, open_count=1)
    assert "1 still open" in help_text and "+0.10" in help_text
    # The value itself stays realized-only, so a moving bid never shifts it.
    assert trade_view.pnl_card_text(s, 0.10, 1)[0] == "+0.30"


def test_pnl_card_names_unpriced_open_positions():
    import trade_pnl
    s = trade_pnl.record_summary([{"pnl": 0.30}])
    _val, help_text = trade_view.pnl_card_text(s, unreal=None, open_count=1)
    assert "1 still open" in help_text


# --- Pooling the cities for "Both" -------------------------------------------

def test_combine_stats_pools_trades_rather_than_averaging_rates():
    """Dallas 1-1 (50%) and Austin 2-0 (100%) pool to 3-1 = 75%, NOT the 75%
    you'd get by luck here from averaging — use lopsided counts to prove it."""
    dal = [{"pnl": 0.30}, {"pnl": -0.20}]
    aus = [{"pnl": 0.10}, {"pnl": 0.10}, {"pnl": 0.10}, {"pnl": 0.10}]
    got = trade_view.combine_stats([
        {"trades": dal, "unreal": None, "open": 0},
        {"trades": aus, "unreal": None, "open": 0}])
    assert got["summary"]["wins"] == 5 and got["summary"]["losses"] == 1
    assert got["summary"]["win_rate"] == 5 / 6      # not (0.5 + 1.0) / 2
    assert got["summary"]["realized"] == 0.50


def test_combine_stats_sums_open_positions_and_their_marks():
    got = trade_view.combine_stats([
        {"trades": [], "unreal": 0.10, "open": 1},
        {"trades": [], "unreal": 0.04, "open": 2}])
    assert got["unreal"] == 0.14 and got["open"] == 3


def test_combine_stats_keeps_unrealized_none_when_nothing_is_priced():
    got = trade_view.combine_stats([
        {"trades": [], "unreal": None, "open": 1},
        {"trades": [], "unreal": None, "open": 1}])
    assert got["unreal"] is None and got["open"] == 2


def test_combine_stats_ignores_unpriced_cities_when_one_is_priced():
    got = trade_view.combine_stats([
        {"trades": [], "unreal": None, "open": 1},
        {"trades": [], "unreal": 0.25, "open": 1}])
    assert got["unreal"] == 0.25


def test_combine_stats_of_one_city_is_that_city():
    trades = [{"pnl": 0.56}]
    got = trade_view.combine_stats([{"trades": trades, "unreal": None, "open": 0}])
    assert got["summary"]["realized"] == 0.56 and got["summary"]["wins"] == 1


def test_combine_stats_of_nothing_is_an_empty_summary():
    got = trade_view.combine_stats([])
    assert got["summary"]["trades"] == 0 and got["unreal"] is None
    assert got["open"] == 0


def test_scope_note_names_one_city_plainly():
    assert trade_view.scope_note(["KDFW"]) == "Dallas"
    assert trade_view.scope_note(["KAUS"]) == "Austin"


def test_scope_note_says_combined_for_both():
    # Without this a pooled Both reads as a single city's numbers.
    assert trade_view.scope_note(["KDFW", "KAUS"]) == "Dallas and Austin combined"


def test_scope_note_of_nothing_is_empty():
    assert trade_view.scope_note([]) == ""


# --- Contract naming + compact reasons --------------------------------------

def test_contract_label_prefers_floor_cap_range():
    assert trade_view._contract_label(
        {"ticker": "KXHIGHAUS-26JUL28-B99.5", "floor": 99, "cap": 100,
         "label": "99° to 100°"}) == "99-100"


def test_contract_label_parses_the_kalshi_label():
    assert trade_view._contract_label(
        {"ticker": "KXHIGHAUS-26JUL28-B99.5", "label": "99° to 100°"}) == "99-100"


def test_contract_label_derives_the_range_from_a_b_ticker():
    # Pre-schema row: B<mid> encodes floor=mid-0.5, cap=mid+0.5.
    assert trade_view._contract_label(
        {"ticker": "KXHIGHAUS-26JUL28-B99.5"}) == "99-100"
    assert trade_view._contract_label(
        {"ticker": "KXLOWTAUS-26JUL28-B75.5"}) == "75-76"


def test_contract_label_keeps_tail_contracts_readable():
    # T-prefixed tails are open-ended and don't follow the B<mid> rule, so with no
    # label there is nothing to widen into a range — show the label when we have it.
    assert trade_view._contract_label(
        {"ticker": "KXHIGHAUS-26JUL28-T97", "label": "96° or below"}) == "96 or below"
    assert trade_view._contract_label({"ticker": "KXHIGHAUS-26JUL28-T97"}) == "T97"


def test_short_reason_compacts_a_target_reversal():
    assert trade_view._short_reason(
        "reversal: target moved to KXHIGHAUS-26JUL28-B97.5") == "reversal → 97-98"


def test_short_reason_compacts_a_stop_loss():
    assert trade_view._short_reason(
        "stop-loss: ask 0.30 <= entry 0.50 - 0.20") == "stop-loss @ 0.30"


def test_short_reason_compacts_a_gate_reversal():
    assert trade_view._short_reason(
        "reversal: safety gate fired") == "reversal · gate fired"


def test_short_reason_leaves_short_text_alone():
    assert trade_view._short_reason("settled won (high 98)") == "settled won (high 98)"
    assert trade_view._short_reason("") == ""


def test_action_rows_use_the_short_reason_and_range_contract():
    actions, _ = trade_view.partition_decisions(_LOG)
    rows = trade_view.action_rows(actions)
    assert rows[0]["Contract"] == "99-100"
    assert rows[1]["Detail"] == "reversal → 99-100 · -0.02"


def test_short_reason_translates_raw_param_names():
    assert trade_view._short_reason("max_open_per_variable") == "position already open"


def test_status_strip_translates_a_raw_skip_reason():
    log = [{"ts": "2026-07-28T22:00:00+00:00", "kind": "skip", "variable": "high",
            "reason": "max_open_per_variable"}]
    assert trade_view.status_strip(log, ["high"])["high"] == "position already open"
