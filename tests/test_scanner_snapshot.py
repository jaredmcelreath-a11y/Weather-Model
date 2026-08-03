from datetime import datetime, timedelta, timezone

import scanner

_NOW = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)
_OPEN = "2026-08-04T06:00:00Z"
_DEAD = (_NOW - timedelta(days=90)).isoformat().replace("+00:00", "Z")


def _market(ticker, bid=0.33, ask=0.37, close=_OPEN, status="active"):
    return {"ticker": ticker, "status": status, "floor_strike": 72,
            "cap_strike": 73, "yes_bid": bid, "yes_ask": ask, "volume": 5,
            "close_time": close}


def _deps(series, markets_by_series, sink):
    return scanner.Deps(
        list_series=lambda: series,
        list_markets=lambda s: markets_by_series.get(s, []),
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

    def markets(s):
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
