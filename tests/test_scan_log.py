import json
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
        self.gets = 0

    def get(self, path):
        self.gets += 1
        if path not in self.files:
            return None
        return self.files[path], "sha-%d" % len(self.files[path])

    def put(self, path, text, sha):
        self.puts += 1
        self.files[path] = text

    def list_dir(self, path):
        prefix = path.rstrip("/") + "/"
        return sorted(p[len(prefix):] for p in self.files if p.startswith(prefix))


def _row(day, i=0):
    return {"ts": f"{day}T12:00:00Z", "i": i}


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


def test_rows_land_in_a_daily_partition():
    assert scan_log.day_path("scan_log.jsonl", _NOW) == "scan_log/2026-08-03.jsonl"


def test_append_partitions_by_the_rows_own_day_not_the_clock():
    # A pass that starts at 23:59 and writes at 00:00 must keep its rows with
    # the firing they belong to.
    t = FakeTransport()
    scan_log.append_many(scan_log.SNAPSHOT_PATH, [_row("2026-08-03")], transport=t,
                         now=datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc))
    assert list(t.files) == ["scan_log/2026-08-03.jsonl"]


def test_each_day_is_its_own_file():
    # The point of the split: no file grows without bound, so no append ever
    # rewrites megabytes -- or trips the contents API's 1 MB JSON ceiling.
    t = FakeTransport()
    for day in ("2026-08-03", "2026-08-03", "2026-08-04"):
        scan_log.append_many(scan_log.SNAPSHOT_PATH, [_row(day)], transport=t)
    assert sorted(t.files) == ["scan_log/2026-08-03.jsonl",
                               "scan_log/2026-08-04.jsonl"]
    assert len(t.files["scan_log/2026-08-03.jsonl"].splitlines()) == 2


def test_load_merges_the_legacy_flat_file_with_every_partition():
    # The pre-split file is never written again but must never be lost either.
    t = FakeTransport()
    t.files["scan_log.jsonl"] = json.dumps(_row("2026-08-01", 1)) + "\n"
    scan_log.append_many(scan_log.SNAPSHOT_PATH, [_row("2026-08-03", 3)], transport=t)
    scan_log.append_many(scan_log.SNAPSHOT_PATH, [_row("2026-08-02", 2)], transport=t)
    assert [r["i"] for r in scan_log.load(scan_log.SNAPSHOT_PATH, transport=t)] == [1, 2, 3]


def test_load_recent_reads_only_the_days_asked_for():
    t = FakeTransport()
    for day, i in (("2026-07-30", 0), ("2026-08-02", 2), ("2026-08-03", 3)):
        scan_log.append_many(scan_log.CANDIDATES_PATH, [_row(day, i)], transport=t)
    t.gets = 0                                      # ignore the writes' own reads
    got = scan_log.load_recent(scan_log.CANDIDATES_PATH, days=2, transport=t, now=_NOW)
    assert [r["i"] for r in got] == [2, 3]          # oldest first, July dropped
    assert t.gets <= 3          # flat file + one per day asked; never a listing


def test_load_recent_drops_stale_rows_from_the_legacy_flat_file():
    t = FakeTransport()
    t.files["scan_candidates.jsonl"] = "".join(
        json.dumps(r) + "\n" for r in (_row("2026-07-01", 1), _row("2026-08-03", 3)))
    got = scan_log.load_recent(scan_log.CANDIDATES_PATH, days=2, transport=t, now=_NOW)
    assert [r["i"] for r in got] == [3]


def test_transport_reads_a_file_too_big_for_the_json_tier():
    """Above 1 MB the contents API returns metadata with an EMPTY content field.
    Decoding that yielded "", which append_many then treated as an empty file and
    PUT over -- silently destroying the log. Fall back to the raw media type."""
    calls = []

    class _Resp:
        def __init__(self, payload=None, text=""):
            self.payload, self.text, self.status_code = payload, text, 200

        def json(self):
            return self.payload

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((headers or {}).get("Accept"))
        if (headers or {}).get("Accept") == "application/vnd.github.raw+json":
            return _Resp(text='{"i": 1}\n')
        return _Resp({"content": "", "encoding": "none", "sha": "abc"})

    t = scan_log.GitHubTransport()
    text, sha = t.get("scan_log.jsonl", _requests_get=fake_get)
    assert (text, sha) == ('{"i": 1}\n', "abc")
    assert "application/vnd.github.raw+json" in calls


def test_snapshot_row_keeps_kalshis_own_bracket_label():
    # A tail's strike sits a degree outside what it pays on, so Kalshi's wording
    # is the only label that matches their site.
    tail = dict(_MARKET, strike_type="greater", floor_strike=90,
                cap_strike=None, yes_sub_title="91° or above")
    row = scan_log.build_snapshot_row(tail, "KXHIGHPHIL", _NOW)
    assert row["label"] == "91° or above"
