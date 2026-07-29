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
