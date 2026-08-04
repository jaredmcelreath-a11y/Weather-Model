from datetime import datetime, timezone

import scan_log

_NOW = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)

# The REAL Kalshi markets payload: prices arrive as dollar STRINGS under the
# *_dollars names, volume under volume_fp. The bare yes_bid/yes_ask/volume keys
# this fixture used to carry do not exist on the live endpoint — a live pass
# against them produced 0 rows from 40 active series (2026-08-03).
_MARKET = {
    "ticker": "KXHIGHDEN-26AUG03-B72.5", "status": "active",
    "strike_type": "between", "floor_strike": 72, "cap_strike": 73,
    "yes_bid_dollars": "0.3300", "yes_ask_dollars": "0.3700",
    "volume_fp": "120.00",
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
    assert row["strike_type"] == "between"
    assert row["yes_bid"] == 0.33 and row["yes_ask"] == 0.37
    assert row["volume"] == 120.0
    assert row["hours_to_close"] == 11.0


def test_open_ended_tail_strikes_survive_a_missing_side():
    # 'greater'/'less' tails carry only one strike; both must still record.
    tail = dict(_MARKET, ticker="KXHIGHDEN-26AUG03-T107",
                strike_type="greater", floor_strike=107, cap_strike=None)
    row = scan_log.build_snapshot_row(tail, "KXHIGHDEN", _NOW)
    assert row["strike_type"] == "greater"
    assert row["floor"] == 107 and row["cap"] is None


def test_an_unquoted_market_is_skipped_not_recorded_as_zero():
    unquoted = {k: v for k, v in _MARKET.items()
                if k not in ("yes_bid_dollars", "yes_ask_dollars")}
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
