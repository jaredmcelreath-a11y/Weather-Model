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
    assert rows[0]["Contract"] == "B97.5"
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
    assert row["Contract"] == "B99.5"       # bracket suffix, no label available
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
     "ticker": "KXHIGHAUS-26JUL28-B97.5", "reason": "reversal: target moved",
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
    assert "B99.5" in out["high"]
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
