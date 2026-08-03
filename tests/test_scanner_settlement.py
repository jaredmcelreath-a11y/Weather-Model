from datetime import datetime, timezone

import scanner

_NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def _settled(ticker, result="no", status="finalized"):
    return {"ticker": ticker, "status": status, "result": result,
            "close_time": "2026-08-04T06:00:00Z"}


def _deps(series, markets_by_series, existing, sink):
    return scanner.Deps(
        list_series=lambda: series,
        list_markets=lambda s, status=None: markets_by_series.get(s, []),
        append_rows=lambda path, rows: sink.extend(rows) or len(rows),
        load_rows=lambda path: existing,
    )


def test_settlement_records_finalized_results():
    sink = []
    d = _deps([{"ticker": "KXHIGHDEN", "title": "Denver"}],
              {"KXHIGHDEN": [_settled("KXHIGHDEN-26AUG03-B72.5", "no"),
                             _settled("KXHIGHDEN-26AUG03-B73.5", "yes")]},
              [], sink)
    out = scanner.settlement_pass(_NOW, d)
    assert out["settled"] == 2
    assert {r["ticker"]: r["result"] for r in sink} == {
        "KXHIGHDEN-26AUG03-B72.5": "no",
        "KXHIGHDEN-26AUG03-B73.5": "yes"}


def test_settlement_skips_tickers_already_recorded():
    sink = []
    existing = [{"ticker": "KXHIGHDEN-26AUG03-B72.5", "result": "no"}]
    d = _deps([{"ticker": "KXHIGHDEN", "title": "Denver"}],
              {"KXHIGHDEN": [_settled("KXHIGHDEN-26AUG03-B72.5", "no"),
                             _settled("KXHIGHDEN-26AUG03-B73.5", "yes")]},
              existing, sink)
    out = scanner.settlement_pass(_NOW, d)
    assert out["settled"] == 1
    assert out["already"] == 1
    assert [r["ticker"] for r in sink] == ["KXHIGHDEN-26AUG03-B73.5"]


def test_settlement_ignores_markets_that_are_not_finalized():
    sink = []
    d = _deps([{"ticker": "KXHIGHDEN", "title": "Denver"}],
              {"KXHIGHDEN": [_settled("KXHIGHDEN-26AUG03-B72.5",
                                      status="active")]},
              [], sink)
    out = scanner.settlement_pass(_NOW, d)
    assert out["settled"] == 0
    assert sink == []


def test_one_broken_series_does_not_kill_the_settlement_pass():
    sink = []

    def markets(s, status=None):
        if s == "KXHIGHBAD":
            raise RuntimeError("kalshi 500")
        return [_settled("KXHIGHDEN-26AUG03-B72.5")]

    d = scanner.Deps(
        list_series=lambda: [{"ticker": "KXHIGHBAD", "title": "bad"},
                             {"ticker": "KXHIGHDEN", "title": "Denver"}],
        list_markets=markets,
        append_rows=lambda path, rows: sink.extend(rows) or len(rows),
        load_rows=lambda path: [],
    )
    out = scanner.settlement_pass(_NOW, d)
    assert out["settled"] == 1
    assert out["errors"] == 1
