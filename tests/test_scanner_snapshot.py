from datetime import datetime, timedelta, timezone

import scanner

_NOW = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)
_OPEN = "2026-08-04T06:00:00Z"
_DEAD = (_NOW - timedelta(days=90)).isoformat().replace("+00:00", "Z")


def _market(ticker, bid="0.3300", ask="0.3700", close=_OPEN, status="active"):
    """Real Kalshi markets shape: dollar strings under *_dollars, volume_fp."""
    m = {"ticker": ticker, "status": status, "strike_type": "between",
         "floor_strike": 72, "cap_strike": 73, "volume_fp": "5.00",
         "close_time": close}
    if bid is not None:
        m["yes_bid_dollars"] = bid
    if ask is not None:
        m["yes_ask_dollars"] = ask
    return m


def _deps(series, markets_by_series, sink):
    return scanner.Deps(
        list_series=lambda: series,
        list_markets=lambda s, status=None: markets_by_series.get(s, []),
        append_rows=lambda path, rows: sink.extend(rows) or len(rows),
    )


def test_snapshot_records_every_priced_bracket():
    sink = []
    d = _deps(
        [{"ticker": "KXHIGHDEN", "title": "Highest temperature in Denver"}],
        {"KXHIGHDEN": [_market("KXHIGHDEN-26AUG03-B72.5"),
                       _market("KXHIGHDEN-26AUG03-B73.5")]},
        sink)
    out = scanner.snapshot_pass(_NOW, d)
    assert out["rows"] == 2
    assert {r["ticker"] for r in sink} == {"KXHIGHDEN-26AUG03-B72.5",
                                           "KXHIGHDEN-26AUG03-B73.5"}
    assert all(r["variable"] == "high" for r in sink)


def test_snapshot_skips_a_dead_series():
    sink = []
    d = _deps(
        [{"ticker": "KXHIGHOLD", "title": "legacy"}],
        {"KXHIGHOLD": [_market("KXHIGHOLD-25JAN01-B1.5",
                               close=_DEAD, status="finalized")]},
        sink)
    out = scanner.snapshot_pass(_NOW, d)
    assert out["rows"] == 0
    assert out["skipped"] == 1
    assert sink == []


def test_snapshot_drops_unquoted_markets_but_keeps_the_rest():
    sink = []
    d = _deps(
        [{"ticker": "KXLOWTDEN", "title": "Lowest temperature in Denver"}],
        {"KXLOWTDEN": [_market("KXLOWTDEN-26AUG03-B60.5"),
                       _market("KXLOWTDEN-26AUG03-B61.5",
                               bid=None, ask=None)]},
        sink)
    out = scanner.snapshot_pass(_NOW, d)
    assert out["rows"] == 1
    assert sink[0]["ticker"] == "KXLOWTDEN-26AUG03-B60.5"
    assert sink[0]["variable"] == "low"


def test_one_broken_series_does_not_kill_the_pass():
    sink = []

    def markets(s, status=None):
        if s == "KXHIGHBAD":
            raise RuntimeError("kalshi 500")
        return [_market("KXHIGHDEN-26AUG03-B72.5")]

    d = scanner.Deps(
        list_series=lambda: [{"ticker": "KXHIGHBAD", "title": "bad"},
                             {"ticker": "KXHIGHDEN", "title": "Denver"}],
        list_markets=markets,
        append_rows=lambda path, rows: sink.extend(rows) or len(rows),
    )
    out = scanner.snapshot_pass(_NOW, d)
    assert out["rows"] == 1                 # Denver still recorded
    assert out["errors"] == 1


def test_snapshot_paces_requests_between_series():
    # Kalshi 429s a full 51-series pass fired back-to-back (measured 2026-08-03:
    # 21 of 26 series lost). Spacing the calls is what makes a whole pass land.
    sleeps = []
    d = scanner.Deps(
        list_series=lambda: [{"ticker": "KXHIGHDEN", "title": "Denver"},
                             {"ticker": "KXLOWTDEN", "title": "Denver low"}],
        list_markets=lambda s, status=None: [_market(f"{s}-26AUG03-B72.5")],
        append_rows=lambda path, rows: len(rows),
        sleep=sleeps.append,
    )
    scanner.snapshot_pass(_NOW, d)
    assert sleeps == [scanner.REQUEST_SPACING_S] * 2


def test_snapshot_requests_only_open_markets():
    # Unfiltered, the endpoint also returns every PAST day's settled markets: a
    # live pass on 2026-08-03 captured 8,000 rows of which 7,520 had already
    # closed. Asking for open markets is what keeps a firing at ~480 rows.
    seen = []

    def markets(s, status=None):
        seen.append(status)
        return [_market(f"{s}-26AUG03-B72.5")]

    d = scanner.Deps(
        list_series=lambda: [{"ticker": "KXHIGHDEN", "title": "Denver"}],
        list_markets=markets,
        append_rows=lambda path, rows: len(rows),
        sleep=lambda s: None,
    )
    scanner.snapshot_pass(_NOW, d)
    assert seen == ["open"]
