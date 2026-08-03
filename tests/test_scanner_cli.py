from datetime import datetime, timezone

import scanner

_NOW = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)


def _deps(sink):
    return scanner.Deps(
        list_series=lambda: [{"ticker": "KXHIGHDEN", "title": "Denver"}],
        list_markets=lambda s, status=None: [
            {"ticker": "KXHIGHDEN-26AUG03-B72.5", "status": "active",
             "strike_type": "between", "floor_strike": 72, "cap_strike": 73,
             "yes_bid_dollars": "0.3300", "yes_ask_dollars": "0.3700",
             "volume_fp": "5.00", "close_time": "2026-08-04T06:00:00Z"}],
        append_rows=lambda path, rows: sink.append((path, rows)) or len(rows),
        load_rows=lambda path: [],
    )


def test_snapshot_command_writes_to_the_snapshot_path():
    sink = []
    assert scanner.main(["snapshot"], deps=_deps(sink), now=_NOW) == 0
    assert sink[0][0] == "scan_log.jsonl"
    assert len(sink[0][1]) == 1


def test_settle_command_writes_to_the_settled_path():
    sink = []
    d = scanner.Deps(
        list_series=lambda: [{"ticker": "KXHIGHDEN", "title": "Denver"}],
        list_markets=lambda s, status=None: [
            {"ticker": "KXHIGHDEN-26AUG03-B72.5", "status": "finalized",
             "result": "no", "close_time": "2026-08-04T06:00:00Z"}],
        append_rows=lambda path, rows: sink.append((path, rows)) or len(rows),
        load_rows=lambda path: [],
    )
    assert scanner.main(["settle"], deps=d, now=_NOW) == 0
    assert sink[0][0] == "scan_settled.jsonl"


def test_an_unknown_command_exits_nonzero():
    assert scanner.main(["frobnicate"], deps=_deps([]), now=_NOW) == 2


def test_no_command_exits_nonzero():
    assert scanner.main([], deps=_deps([]), now=_NOW) == 2
