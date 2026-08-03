from datetime import datetime, timezone

import scan_log

_NOW = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)

_MARKET = {
    "ticker": "KXHIGHDEN-26AUG03-B72.5", "status": "active",
    "floor_strike": 72, "cap_strike": 73,
    "yes_bid": 0.33, "yes_ask": 0.37, "volume": 120,
    "close_time": "2026-08-04T06:00:00Z",
}


class FakeTransport:
    """In-memory stand-in for the GitHub contents API."""

    def __init__(self):
        self.files = {}
        self.puts = 0

    def get(self, path):
        if path not in self.files:
            return None
        return self.files[path], "sha-%d" % len(self.files[path])

    def put(self, path, text, sha):
        self.puts += 1
        self.files[path] = text


def test_variable_comes_from_the_series_prefix():
    assert scan_log.variable_of_series("KXHIGHDEN") == "high"
    assert scan_log.variable_of_series("KXLOWTDEN") == "low"
    assert scan_log.variable_of_series("KXBTCD") is None


def test_hours_to_close_is_positive_before_close():
    assert scan_log.hours_to_close("2026-08-04T06:00:00Z", _NOW) == 11.0


def test_snapshot_row_carries_price_strike_and_hours_to_close():
    row = scan_log.build_snapshot_row(_MARKET, "KXHIGHDEN", _NOW)
    assert row["ticker"] == "KXHIGHDEN-26AUG03-B72.5"
    assert row["series"] == "KXHIGHDEN"
    assert row["variable"] == "high"
    assert row["floor"] == 72 and row["cap"] == 73
    assert row["yes_bid"] == 0.33 and row["yes_ask"] == 0.37
    assert row["hours_to_close"] == 11.0


def test_an_unquoted_market_is_skipped_not_recorded_as_zero():
    unquoted = dict(_MARKET, yes_bid=None, yes_ask=None)
    assert scan_log.build_snapshot_row(unquoted, "KXHIGHDEN", _NOW) is None


def test_settlement_row_only_for_a_finalized_market():
    settled = dict(_MARKET, status="finalized", result="no")
    row = scan_log.build_settlement_row(settled, _NOW)
    assert row["ticker"] == "KXHIGHDEN-26AUG03-B72.5"
    assert row["result"] == "no"

    assert scan_log.build_settlement_row(_MARKET, _NOW) is None


def test_append_many_writes_every_row_in_a_single_put():
    t = FakeTransport()
    rows = [{"i": i} for i in range(600)]
    n = scan_log.append_many(scan_log.SNAPSHOT_PATH, rows, transport=t)
    assert n == 600
    assert t.puts == 1                      # one PUT, not 600
    assert len(scan_log.load(scan_log.SNAPSHOT_PATH, transport=t)) == 600


def test_append_many_appends_to_existing_content():
    t = FakeTransport()
    scan_log.append_many(scan_log.SNAPSHOT_PATH, [{"i": 1}], transport=t)
    scan_log.append_many(scan_log.SNAPSHOT_PATH, [{"i": 2}], transport=t)
    got = scan_log.load(scan_log.SNAPSHOT_PATH, transport=t)
    assert [r["i"] for r in got] == [1, 2]


def test_append_many_with_no_rows_writes_nothing():
    t = FakeTransport()
    assert scan_log.append_many(scan_log.SNAPSHOT_PATH, [], transport=t) == 0
    assert t.puts == 0


def test_load_of_a_missing_file_is_empty():
    assert scan_log.load(scan_log.SNAPSHOT_PATH, transport=FakeTransport()) == []
