import scan_report


def _row(ticker, bid, ask, hours, series="KXHIGHDEN", variable="high"):
    return {"ticker": ticker, "series": series, "variable": variable,
            "yes_bid": bid, "yes_ask": ask, "hours_to_close": hours}


def test_mid_is_the_average_of_bid_and_ask():
    assert scan_report.mid(_row("t", 0.30, 0.40, 5)) == 0.35


def test_mid_falls_back_to_the_single_side_available():
    assert scan_report.mid(_row("t", None, 0.40, 5)) == 0.40
    assert scan_report.mid(_row("t", 0.30, None, 5)) == 0.30


def test_no_cost_is_one_minus_the_yes_bid():
    # Fading a bracket means BUYING NO, which sells against the YES bid.
    assert scan_report.no_cost(_row("t", 0.30, 0.40, 5)) == 0.70


def test_reliability_counts_hits_in_the_right_band():
    rows = [_row("a", 0.30, 0.40, 5),      # mid 0.35 -> band (0.30,0.45)
            _row("b", 0.30, 0.40, 5),
            _row("c", 0.10, 0.20, 5)]      # mid 0.15 -> band (0.15,0.30)
    settled = [{"ticker": "a", "result": "yes"},
               {"ticker": "b", "result": "no"},
               {"ticker": "c", "result": "no"}]
    stats = scan_report.reliability(rows, settled)
    band = next(s for s in stats if s["band"] == (0.30, 0.45))
    assert band["n_observations"] == 2
    assert band["settled_yes"] == 1
    assert band["hit_rate"] == 0.5


def test_reliability_separates_unique_brackets_from_observations():
    # The same bracket snapshotted three times is three correlated observations
    # of ONE outcome; reporting only n_observations would overstate the sample.
    rows = [_row("a", 0.30, 0.40, 20),
            _row("a", 0.30, 0.40, 12),
            _row("a", 0.30, 0.40, 4)]
    settled = [{"ticker": "a", "result": "no"}]
    stats = scan_report.reliability(rows, settled, hour_buckets=[(0, 36)])
    band = next(s for s in stats if s["band"] == (0.30, 0.45))
    assert band["n_observations"] == 3
    assert band["n_unique_brackets"] == 1


def test_reliability_buckets_by_hours_to_close():
    rows = [_row("a", 0.30, 0.40, 2), _row("b", 0.30, 0.40, 20)]
    settled = [{"ticker": "a", "result": "yes"}, {"ticker": "b", "result": "no"}]
    stats = scan_report.reliability(rows, settled)
    near = next(s for s in stats
                if s["hours"] == (0, 6) and s["band"] == (0.30, 0.45))
    far = next(s for s in stats
               if s["hours"] == (18, 36) and s["band"] == (0.30, 0.45))
    assert near["n_observations"] == 1 and near["settled_yes"] == 1
    assert far["n_observations"] == 1 and far["settled_yes"] == 0


def test_rows_without_a_settlement_are_excluded():
    rows = [_row("a", 0.30, 0.40, 5), _row("unsettled", 0.30, 0.40, 5)]
    settled = [{"ticker": "a", "result": "no"}]
    stats = scan_report.reliability(rows, settled)
    band = next(s for s in stats if s["band"] == (0.30, 0.45))
    assert band["n_observations"] == 1


def test_format_table_names_the_series_and_the_rate():
    stats = scan_report.reliability(
        [_row("a", 0.30, 0.40, 5)], [{"ticker": "a", "result": "no"}])
    out = scan_report.format_table(stats)
    assert "KXHIGHDEN" in out
    assert "0.0%" in out
