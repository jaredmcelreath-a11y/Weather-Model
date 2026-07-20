import edge_report


def _row(slot, consensus, settled, asks=None, **kw):
    r = {"capture_slot": slot, "variable": "high", "cli_consensus": consensus,
         "settled_cli": settled, "settled_hourly": settled - 1.0,
         "actual_gap": 1.0, "market_ev": consensus, "flat_offset": 0.89,
         "market_buckets": [[None, 98, 0.1], [99, 100, 0.9]]}
    if asks is not None:
        r["market_asks"] = asks
    r.update(kw)
    return r


def test_settled_bucket_ask_picks_the_winning_bracket():
    rows = [_row("close-15", 99.4, 99.0,
                 asks=[[None, 98, 0.02, 0.05], [99, 100, 0.93, 0.97]])]
    m = edge_report._subset_metrics(rows, "high")
    assert m["settled_bucket_ask"] == 0.97
    assert m["n_settled_ask"] == 1


def test_settled_bucket_ask_averages_and_tracks_the_minimum():
    rows = [_row("close-15", 99.4, 99.0, asks=[[99, 100, 0.90, 0.96]]),
            _row("close-15", 99.4, 99.0, asks=[[99, 100, 0.80, 0.84]])]
    m = edge_report._subset_metrics(rows, "high")
    assert m["settled_bucket_ask"] == 0.90       # (0.96 + 0.84) / 2
    assert m["settled_bucket_ask_min"] == 0.84
    assert m["n_settled_ask"] == 2


def test_open_ended_bracket_matches():
    rows = [_row("close-15", 97.0, 97.0, asks=[[None, 98, 0.88, 0.92]])]
    assert edge_report._subset_metrics(rows, "high")["settled_bucket_ask"] == 0.92


def test_rows_without_asks_report_none():
    m = edge_report._subset_metrics([_row("15:30", 99.4, 99.0)], "high")
    assert m["settled_bucket_ask"] is None
    assert m["n_settled_ask"] == 0


def test_missing_ask_price_is_skipped_not_counted():
    rows = [_row("close-15", 99.4, 99.0, asks=[[99, 100, 0.90, None]])]
    m = edge_report._subset_metrics(rows, "high")
    assert m["settled_bucket_ask"] is None
    assert m["n_settled_ask"] == 0


def test_family_order_puts_day_ahead_first_and_close_last():
    assert edge_report._family("eve-22:00") == 0
    assert edge_report._family("15:30") == 1
    assert edge_report._family("sr-30") == 1
    assert edge_report._family("close-15") == 2


def test_report_orders_blocks_by_family(tmp_path):
    metrics = {}
    for slot in ("15:30", "close-15", "eve-22:00"):
        metrics[(slot, "high", "all")] = edge_report._subset_metrics(
            [_row(slot, 99.4, 99.0)], "high")
    _csv, md = edge_report.write_report(metrics, str(tmp_path))
    text = open(md).read()
    assert text.index("eve-22:00") < text.index("15:30") < text.index("close-15")


def test_settled_ask_appears_in_the_assessment(tmp_path):
    metrics = {("close-15", "high", "all"): edge_report._subset_metrics(
        [_row("close-15", 99.4, 99.0, asks=[[99, 100, 0.93, 0.97]])], "high")}
    _csv, md = edge_report.write_report(metrics, str(tmp_path))
    assert "settled bracket ask" in open(md).read()
