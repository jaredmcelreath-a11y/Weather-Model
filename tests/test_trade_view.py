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
    assert rows[0]["Ticker"] == "KXHIGHAUS-26JUL28-B97.5"
    assert rows[0]["Side"] == "Yes"
    assert rows[0]["Count"] == 1
    assert "0.57" in str(rows[0]["Entry Ask"])


def test_position_rows_live_reads_the_account_enriched_with_entry():
    truth = [{"ticker": "KXHIGHAUS-26JUL28-B97.5", "side": "yes", "count": 2,
              "variable": "high"}]
    rows = trade_view.position_rows("live", truth, _RUNTIME)
    assert len(rows) == 1 and rows[0]["Count"] == 2      # the account is the truth
    assert "0.57" in str(rows[0]["Entry Ask"])


def test_position_rows_live_ignores_the_shadow_record():
    # Nothing actually held -> nothing shown, even with a stale runtime entry.
    assert trade_view.position_rows("live", [], _RUNTIME) == []


def test_position_rows_tolerates_a_missing_entry_ask():
    rows = trade_view.position_rows("live", [{"ticker": "X", "side": "no",
                                              "count": 1, "variable": "low"}], {})
    assert rows[0]["Entry Ask"] == "—"
