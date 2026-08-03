# Kalshi Multi-City Price Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log every Kalshi city temperature bracket's price three times a day, join it to Kalshi's own settlement result, and report whether tails are systematically overpriced — per city, with no weather model involved.

**Architecture:** Three new top-level modules (`scan_log.py` schema+IO, `scanner.py` orchestration, `scan_report.py` analysis) plus two pure additions to `sources/kalshi.py`. Data lands on a dedicated `scan-data` git branch via the GitHub contents API. Read-only: nothing imports `trader.py` or `kalshi_orders.py`.

**Tech Stack:** Python 3.9-compatible (local venv is 3.9.6; CI uses 3.11), `pytest`, `requests`, existing `sources.common.get_json`.

## Global Constraints

- **Read-only.** No module in this plan may import `trader`, `trade_logic`, `trade_state`, `kalshi_orders`, or `trade_params`. No order placement. No writes to `trade-data`.
- **Python 3.9 compatible.** No `match`, no `X | Y` in runtime-evaluated positions without `from __future__ import annotations` (every new module must include that import, matching the codebase).
- **No per-city config.** Nothing in this plan may add a `StationConfig` entry or read `config.STATIONS`. City identity is the Kalshi series ticker.
- **No Open-Meteo, no NWS.** Kalshi is the only data source.
- **Excluded series:** `KXHIGHUS` (national), `KXHIGHNYD` (hourly directional).
- **Snapshot firings:** 12:00, 18:00, 00:00 UTC. Settlement pass: 12:00 UTC.
- **Activity window:** a series counts as active if it has an open market or one whose `close_time` is within the last **7 days**.
- **Price bands:** `[(0.05,0.15), (0.15,0.30), (0.30,0.45), (0.45,0.60), (0.60,0.80), (0.80,0.95)]`
- **Hours-to-close buckets:** `[(0,6), (6,18), (18,36)]`
- Tests live in `tests/`, run with `python3 -m pytest`. Full suite must stay green (950 passing as of `bb5bf47`).

---

### Task 1: Kalshi series discovery and market listing

**Files:**
- Modify: `sources/kalshi.py` (append new functions at end of file)
- Test: `tests/test_kalshi_scan_series.py` (create)

**Interfaces:**
- Consumes: `BASE`, `get_json` (already in `sources/kalshi.py`)
- Produces:
  - `list_weather_series(fetch=None) -> list[dict]` — each `{"ticker": str, "title": str}`, sorted by ticker
  - `list_series_markets(series_ticker: str, status: str | None = None, fetch=None) -> list[dict]` — raw market dicts
  - `is_series_active(markets: list[dict], now: datetime, window_days: int = 7) -> bool`
  - `parse_kalshi_ts(value: str | None) -> datetime | None` — ISO-8601 with `Z`, returns tz-aware UTC

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kalshi_scan_series.py`:

```python
from datetime import datetime, timedelta, timezone

from sources import kalshi

_NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)

_SERIES_PAYLOAD = {"series": [
    {"ticker": "KXHIGHDEN", "title": "Highest temperature in Denver"},
    {"ticker": "KXLOWTDEN", "title": "Lowest temperature in Denver"},
    {"ticker": "KXHIGHTEMPDEN", "title": "High temperature denver"},
    {"ticker": "KXHIGHUS", "title": "High temp in United States"},
    {"ticker": "KXHIGHNYD", "title": "Hourly Directional NYC Temperature"},
    {"ticker": "KXBTCD", "title": "Bitcoin price"},
]}


def test_discovery_keeps_city_high_low_series_only():
    got = kalshi.list_weather_series(fetch=lambda: _SERIES_PAYLOAD)
    assert [s["ticker"] for s in got] == [
        "KXHIGHDEN", "KXHIGHTEMPDEN", "KXLOWTDEN"]


def test_discovery_carries_the_title():
    got = kalshi.list_weather_series(fetch=lambda: _SERIES_PAYLOAD)
    assert got[0]["title"] == "Highest temperature in Denver"


def test_a_series_with_an_open_market_is_active():
    markets = [{"status": "active", "close_time": None}]
    assert kalshi.is_series_active(markets, _NOW) is True


def test_a_series_that_closed_inside_the_window_is_active():
    recent = (_NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    markets = [{"status": "finalized", "close_time": recent}]
    assert kalshi.is_series_active(markets, _NOW) is True


def test_a_long_dead_series_is_not_active():
    old = (_NOW - timedelta(days=90)).isoformat().replace("+00:00", "Z")
    markets = [{"status": "finalized", "close_time": old}]
    assert kalshi.is_series_active(markets, _NOW) is False


def test_no_markets_at_all_is_not_active():
    assert kalshi.is_series_active([], _NOW) is False


def test_list_series_markets_passes_the_series_and_status():
    seen = {}

    def fake(params):
        seen.update(params)
        return {"markets": [{"ticker": "KXHIGHDEN-26AUG03-B72.5"}]}

    got = kalshi.list_series_markets("KXHIGHDEN", status="settled", fetch=fake)
    assert seen["series_ticker"] == "KXHIGHDEN"
    assert seen["status"] == "settled"
    assert got[0]["ticker"] == "KXHIGHDEN-26AUG03-B72.5"


def test_parse_kalshi_ts_handles_the_z_suffix():
    got = kalshi.parse_kalshi_ts("2026-08-04T06:00:00Z")
    assert got == datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)


def test_parse_kalshi_ts_returns_none_for_junk():
    assert kalshi.parse_kalshi_ts(None) is None
    assert kalshi.parse_kalshi_ts("not a date") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_kalshi_scan_series.py -v`
Expected: FAIL — `AttributeError: module 'sources.kalshi' has no attribute 'list_weather_series'`

- [ ] **Step 3: Write minimal implementation**

Append to the end of `sources/kalshi.py`:

```python
# ---- Multi-city scanner support -------------------------------------------
# Read-only discovery for the price scanner. Kept here (not in a station config)
# because the scanner deliberately has no per-city setup: city identity IS the
# series ticker.

WEATHER_CATEGORY = "Climate and Weather"

# Not daily city high/low markets: national aggregate, and an hourly directional
# NYC series that shares the KXHIGH prefix.
EXCLUDED_SERIES = {"KXHIGHUS", "KXHIGHNYD"}


def parse_kalshi_ts(value):
    """Kalshi's ISO-8601 timestamps ('2026-08-04T06:00:00Z') as aware UTC
    datetimes. None (not an exception) for missing or unparseable input, so a
    single malformed market never kills a scan pass."""
    from datetime import datetime as _dt
    if not value:
        return None
    try:
        return _dt.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def list_weather_series(fetch=None) -> list:
    """Daily city high/low temperature series, as [{"ticker", "title"}, ...].

    Discovered from the live category rather than hardcoded: the real list is
    full of legacy duplicates (KXHIGHDEN beside KXHIGHTEMPDEN, three Houston
    variants) and the naming is inconsistent even within a city (KXHIGHAUS but
    KXLOWTAUS). A hardcoded table would encode today's mess and rot. Callers
    filter to live ones with `is_series_active`."""
    fetch = fetch or (lambda: get_json(f"{BASE}/series",
                                       {"category": WEATHER_CATEGORY}, ttl=3600))
    out = []
    for s in (fetch() or {}).get("series") or []:
        ticker = (s.get("ticker") or "").upper()
        if ticker in EXCLUDED_SERIES:
            continue
        if not (ticker.startswith("KXHIGH") or ticker.startswith("KXLOW")):
            continue
        out.append({"ticker": ticker, "title": s.get("title") or ""})
    return sorted(out, key=lambda s: s["ticker"])


def list_series_markets(series_ticker: str, status=None, fetch=None) -> list:
    """Every market under `series_ticker`, optionally filtered by status.

    One call returns the whole bracket ladder, which is why the scanner needs no
    per-bracket requests. `fetch` takes the params dict, for tests."""
    params = {"series_ticker": series_ticker, "limit": 200}
    if status:
        params["status"] = status
    fetch = fetch or (lambda p: get_json(f"{BASE}/markets", p, ttl=60))
    return (fetch(params) or {}).get("markets") or []


def is_series_active(markets: list, now, window_days: int = 7) -> bool:
    """True when the series has a live market or one that closed recently.

    Drops the dead legacy series without hardcoding which ones they are."""
    from datetime import timedelta as _td
    cutoff = now - _td(days=window_days)
    for m in markets:
        if (m.get("status") or "").lower() in ("open", "active"):
            return True
        closed = parse_kalshi_ts(m.get("close_time"))
        if closed is not None and closed >= cutoff:
            return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_kalshi_scan_series.py -v`
Expected: 9 passed

Then run the full suite to confirm nothing regressed:
Run: `python3 -m pytest`
Expected: 959 passed

- [ ] **Step 5: Commit**

```bash
git add sources/kalshi.py tests/test_kalshi_scan_series.py
git commit -m "feat(scanner): discover active Kalshi city temperature series"
```

---

### Task 2: Scan log schema and batched branch IO

**Files:**
- Create: `scan_log.py`
- Test: `tests/test_scan_log.py` (create)

**Interfaces:**
- Consumes: `sources.kalshi.parse_kalshi_ts` (Task 1)
- Produces:
  - `SNAPSHOT_PATH = "scan_log.jsonl"`, `SETTLED_PATH = "scan_settled.jsonl"`
  - `variable_of_series(series: str) -> str | None`
  - `hours_to_close(close_time, now) -> float | None`
  - `build_snapshot_row(market: dict, series: str, now) -> dict | None`
  - `build_settlement_row(market: dict, now) -> dict | None`
  - `append_many(path: str, rows: list, transport=None) -> int`
  - `load(path: str, transport=None) -> list`
  - `GitHubTransport` (class, `.get(path)`/`.put(path, text, sha)`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scan_log.py`:

```python
import json
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scan_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scan_log'`

- [ ] **Step 3: Write minimal implementation**

Create `scan_log.py`:

```python
"""Record schema and branch-backed IO for the multi-city price scanner.

Two append-only files on a dedicated `scan-data` branch:

`scan_log.jsonl`     — one row per bracket per snapshot firing (price + strike).
`scan_settled.jsonl` — one row per bracket, Kalshi's own settlement result.

They are joined on `ticker` at report time, the same shape as
betting_log.jsonl + settlements.jsonl.

The transport duplicates trade_state.GitHubTransport rather than importing it.
That is deliberate: the trader's IO is load-bearing for real money, and reusing
it would create a path where a scanner change breaks trading. Different env
vars, different branch, no shared code.
"""
from __future__ import annotations

import base64
import json
import os

import requests

from sources.kalshi import parse_kalshi_ts

SNAPSHOT_PATH = "scan_log.jsonl"
SETTLED_PATH = "scan_settled.jsonl"


class GitHubTransport:
    """GET/PUT a file on the scan-data branch via the contents API."""

    def __init__(self):
        self.repo = os.environ.get("SCAN_GH_REPO", "")
        self.branch = os.environ.get("SCAN_GH_BRANCH", "scan-data")
        self.token = os.environ.get("SCAN_GH_TOKEN", "")

    def _headers(self):
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _url(self, path):
        return f"https://api.github.com/repos/{self.repo}/contents/{path}"

    def get(self, path):
        r = requests.get(self._url(path), params={"ref": self.branch},
                         headers=self._headers(), timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        j = r.json()
        return base64.b64decode(j["content"]).decode("utf-8"), j["sha"]

    def put(self, path, text, sha):
        body = {"message": f"scan: update {path}", "branch": self.branch,
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        r = requests.put(self._url(path), json=body,
                         headers=self._headers(), timeout=30)
        r.raise_for_status()


def _t(transport=None):
    return transport or GitHubTransport()


def variable_of_series(series: str):
    """'high'/'low' from the series prefix, or None for a non-temperature series."""
    s = (series or "").upper()
    if s.startswith("KXHIGH"):
        return "high"
    if s.startswith("KXLOW"):
        return "low"
    return None


def hours_to_close(close_time, now):
    """Hours from `now` until the market closes, or None when unparseable.

    This is the scanner's time axis instead of local clock time: it needs no
    per-city timezone table, and '12 hours before settlement' is comparable
    across cities in a way that '13:00 local' is not."""
    closed = parse_kalshi_ts(close_time)
    if closed is None:
        return None
    return round((closed - now).total_seconds() / 3600.0, 2)


def build_snapshot_row(market: dict, series: str, now):
    """One priced bracket at one moment, or None when the market has no quotes.

    An unquoted market must be dropped, not stored: recording a missing bid as
    0 would look like a free option in the reliability curve."""
    bid, ask = market.get("yes_bid"), market.get("yes_ask")
    if bid is None and ask is None:
        return None
    return {
        "ts": now.isoformat().replace("+00:00", "Z"),
        "series": series,
        "variable": variable_of_series(series),
        "ticker": market.get("ticker"),
        "floor": market.get("floor_strike"),
        "cap": market.get("cap_strike"),
        "yes_bid": bid,
        "yes_ask": ask,
        "volume": market.get("volume"),
        "close_time": market.get("close_time"),
        "hours_to_close": hours_to_close(market.get("close_time"), now),
    }


def build_settlement_row(market: dict, now):
    """Kalshi's own outcome for a bracket, or None if it has not finalized.

    Only `finalized` counts — a market can be `settled` and still revise."""
    if (market.get("status") or "").lower() != "finalized":
        return None
    result = (market.get("result") or "").lower()
    if result not in ("yes", "no"):
        return None
    return {"ticker": market.get("ticker"), "result": result,
            "settled_at": now.isoformat().replace("+00:00", "Z")}


def load(path: str, transport=None) -> list:
    """Every row in `path`, oldest first; [] when the file does not exist."""
    got = _t(transport).get(path)
    if not got:
        return []
    return [json.loads(l) for l in got[0].splitlines() if l.strip()]


def append_many(path: str, rows: list, transport=None) -> int:
    """Append every row in ONE read + ONE write.

    trade_state.append_jsonl does a GET+PUT per record, which is fine for the
    trader's handful of rows a day. A snapshot pass writes ~600 at once, so
    per-record round trips would mean 600 API calls and 600 commits per firing.
    """
    if not rows:
        return 0
    t = _t(transport)
    got = t.get(path)
    text, sha = (got[0], got[1]) if got else ("", None)
    payload = "".join(json.dumps(r) + "\n" for r in rows)
    t.put(path, (text + payload) if text else payload, sha)
    return len(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scan_log.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add scan_log.py tests/test_scan_log.py
git commit -m "feat(scanner): scan log schema and batched scan-data branch IO"
```

---

### Task 3: Snapshot pass

**Files:**
- Create: `scanner.py`
- Test: `tests/test_scanner_snapshot.py` (create)

**Interfaces:**
- Consumes: `sources.kalshi.list_weather_series`, `list_series_markets`, `is_series_active` (Task 1); `scan_log.build_snapshot_row`, `append_many`, `SNAPSHOT_PATH` (Task 2)
- Produces:
  - `Deps` (dataclass with callables `list_series`, `list_markets`, `append_rows`)
  - `snapshot_pass(now, deps) -> dict` returning `{"series": int, "rows": int, "skipped": int}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scanner_snapshot.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scanner_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scanner'`

- [ ] **Step 3: Write minimal implementation**

Create `scanner.py`:

```python
"""Read-only multi-city Kalshi price scanner.

Snapshots every city temperature bracket's price a few times a day and, after
close, records Kalshi's own settlement result. The pair answers one question:
are these markets' tails systematically overpriced, and in which cities?

Deliberately model-free. It reads no weather data and holds no per-city config —
city identity is the Kalshi series ticker, and the time axis is hours-to-close
from each market's own close_time. That is what makes ~20 cities cost nothing.

Read-only by construction: imports nothing from the trading modules and places
no orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import scan_log
from sources import kalshi


@dataclass
class Deps:
    """Injected IO, so a pass is testable without network or git."""
    list_series: Callable
    list_markets: Callable
    append_rows: Callable


def _real_deps() -> Deps:
    return Deps(
        list_series=kalshi.list_weather_series,
        list_markets=kalshi.list_series_markets,
        append_rows=lambda path, rows: scan_log.append_many(path, rows),
    )


def snapshot_pass(now: datetime, deps: Deps) -> dict:
    """Price every bracket of every active city series, once.

    A failing series is counted and skipped rather than raised: one city's
    outage must not cost the other nineteen their snapshot."""
    rows, skipped, errors = [], 0, 0
    for s in deps.list_series():
        ticker = s["ticker"]
        try:
            markets = deps.list_markets(ticker)
        except Exception as e:            # noqa: BLE001 - see docstring
            print(f"[scan] {ticker}: markets unavailable ({e})")
            errors += 1
            continue
        if not kalshi.is_series_active(markets, now):
            skipped += 1
            continue
        for m in markets:
            row = scan_log.build_snapshot_row(m, ticker, now)
            if row is not None:
                rows.append(row)
    written = deps.append_rows(scan_log.SNAPSHOT_PATH, rows)
    return {"series": len({r["series"] for r in rows}),
            "rows": written or 0, "skipped": skipped, "errors": errors}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scanner_snapshot.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add scanner.py tests/test_scanner_snapshot.py
git commit -m "feat(scanner): snapshot pass over every active city series"
```

---

### Task 4: Settlement pass

**Files:**
- Modify: `scanner.py` (add `settlement_pass`; extend `Deps` and `_real_deps`)
- Test: `tests/test_scanner_settlement.py` (create)

**Interfaces:**
- Consumes: everything from Task 3, plus `scan_log.build_settlement_row`, `scan_log.load`, `scan_log.SETTLED_PATH`
- Produces:
  - `Deps` gains `load_rows: Callable` (signature `load_rows(path) -> list`)
  - `settlement_pass(now, deps) -> dict` returning `{"settled": int, "already": int, "errors": int}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scanner_settlement.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scanner_settlement.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'load_rows'`

- [ ] **Step 3: Write minimal implementation**

In `scanner.py`, add the new field to `Deps`. It gets a default so Task 3's
snapshot tests, which construct `Deps` with three arguments, keep passing:

```python
@dataclass
class Deps:
    """Injected IO, so a pass is testable without network or git."""
    list_series: Callable
    list_markets: Callable
    append_rows: Callable
    load_rows: Callable = lambda path: []
```

Update `_real_deps` to supply it:

```python
def _real_deps() -> Deps:
    return Deps(
        list_series=kalshi.list_weather_series,
        list_markets=kalshi.list_series_markets,
        append_rows=lambda path, rows: scan_log.append_many(path, rows),
        load_rows=lambda path: scan_log.load(path),
    )
```

Then append `settlement_pass`:

```python
def settlement_pass(now: datetime, deps: Deps) -> dict:
    """Record Kalshi's own outcome for every newly finalized bracket.

    Kalshi self-reports settlement (`result` + `status: finalized`), which is why
    the scanner needs no NWS CLI access and no per-city settlement basis. Tickers
    already on file are skipped, so the pass is safe to re-run."""
    known = {r.get("ticker") for r in deps.load_rows(scan_log.SETTLED_PATH)}
    rows, already, errors = [], 0, 0
    for s in deps.list_series():
        ticker = s["ticker"]
        try:
            markets = deps.list_markets(ticker, status="settled")
        except Exception as e:            # noqa: BLE001 - one city must not
            print(f"[scan] {ticker}: settled markets unavailable ({e})")
            errors += 1                   # cost the others their settlement
            continue
        for m in markets:
            row = scan_log.build_settlement_row(m, now)
            if row is None:
                continue
            if row["ticker"] in known:
                already += 1
                continue
            known.add(row["ticker"])
            rows.append(row)
    written = deps.append_rows(scan_log.SETTLED_PATH, rows)
    return {"settled": written or 0, "already": already, "errors": errors}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scanner_settlement.py tests/test_scanner_snapshot.py -v`
Expected: 8 passed

Note: `snapshot_pass` calls `deps.list_markets(ticker)` with one argument while
`settlement_pass` calls it with `status=`. The real
`kalshi.list_series_markets(series_ticker, status=None, fetch=None)` accepts
both, and the snapshot tests' fake takes one argument. That is intentional — do
not "fix" either call site.

- [ ] **Step 5: Commit**

```bash
git add scanner.py tests/test_scanner_settlement.py
git commit -m "feat(scanner): settlement pass using Kalshi's own finalized results"
```

---

### Task 5: Reliability report

**Files:**
- Create: `scan_report.py`
- Test: `tests/test_scan_report.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks at runtime — operates on plain row dicts of the shape `build_snapshot_row` produces and `build_settlement_row` produces
- Produces:
  - `PRICE_BANDS`, `HOUR_BUCKETS` (module constants)
  - `mid(row) -> float | None`
  - `no_cost(row) -> float | None`
  - `reliability(rows: list, settled: list, bands=PRICE_BANDS, hour_buckets=HOUR_BUCKETS) -> list[dict]`
  - `format_table(stats: list) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scan_report.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scan_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scan_report'`

- [ ] **Step 3: Write minimal implementation**

Create `scan_report.py`:

```python
"""Reliability curve for the multi-city scanner.

Of the brackets priced in a given band, what fraction actually settled YES? A
well-calibrated market lands on the diagonal; the favorite-longshot bias predicts
that low-priced brackets settle LESS often than their price implies, which is the
inefficiency a fade strategy would harvest.
"""
from __future__ import annotations

PRICE_BANDS = [(0.05, 0.15), (0.15, 0.30), (0.30, 0.45),
               (0.45, 0.60), (0.60, 0.80), (0.80, 0.95)]

HOUR_BUCKETS = [(0, 6), (6, 18), (18, 36)]


def mid(row: dict):
    """Fair-value price: the bid/ask midpoint, or whichever side exists alone."""
    bid, ask = row.get("yes_bid"), row.get("yes_ask")
    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 4)
    return ask if ask is not None else bid


def no_cost(row: dict):
    """What FADING this bracket would actually have cost: buying NO sells against
    the YES bid, so the price paid is 1 - yes_bid. The mid says whether a market
    is fair; this says whether the edge survives the spread."""
    bid = row.get("yes_bid")
    return None if bid is None else round(1.0 - bid, 4)


def _bucket(value, buckets):
    for lo, hi in buckets:
        if lo <= value < hi:
            return (lo, hi)
    return None


def reliability(rows: list, settled: list, bands=PRICE_BANDS,
                hour_buckets=HOUR_BUCKETS) -> list:
    """Observed settle-YES rate per series x variable x price band x hours-to-close.

    Rows with no settlement on file are excluded — an unsettled bracket has no
    outcome to score against, and counting it would silently deflate every rate.
    """
    outcome = {r.get("ticker"): (r.get("result") or "").lower() for r in settled}
    groups: dict = {}
    for row in rows:
        result = outcome.get(row.get("ticker"))
        if result not in ("yes", "no"):
            continue
        price = mid(row)
        hours = row.get("hours_to_close")
        if price is None or hours is None:
            continue
        band = _bucket(price, bands)
        hbucket = _bucket(hours, hour_buckets)
        if band is None or hbucket is None:
            continue
        key = (row.get("series"), row.get("variable"), band, hbucket)
        g = groups.setdefault(key, {"n": 0, "yes": 0, "tickers": set(),
                                    "no_costs": []})
        g["n"] += 1
        g["yes"] += 1 if result == "yes" else 0
        g["tickers"].add(row.get("ticker"))
        cost = no_cost(row)
        if cost is not None:
            g["no_costs"].append(cost)

    out = []
    for (series, variable, band, hours), g in sorted(
            groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][3], kv[0][2])):
        costs = g["no_costs"]
        out.append({
            "series": series, "variable": variable, "band": band, "hours": hours,
            "n_observations": g["n"],
            "n_unique_brackets": len(g["tickers"]),
            "settled_yes": g["yes"],
            "hit_rate": round(g["yes"] / g["n"], 4),
            "mid_band_center": round((band[0] + band[1]) / 2.0, 4),
            "mean_no_cost": round(sum(costs) / len(costs), 4) if costs else None,
        })
    return out


def format_table(stats: list) -> str:
    """Fixed-width rendering for the Action log."""
    header = (f"{'series':<14} {'var':<5} {'band':<12} {'hrs':<8} "
              f"{'n':>5} {'brk':>5} {'settled':>8} {'implied':>8} {'noCost':>7}")
    lines = [header, "-" * len(header)]
    for s in stats:
        band = f"{s['band'][0]:.0%}-{s['band'][1]:.0%}"
        hrs = f"{s['hours'][0]}-{s['hours'][1]}h"
        cost = "—" if s["mean_no_cost"] is None else f"{s['mean_no_cost']:.2f}"
        lines.append(
            f"{s['series']:<14} {s['variable']:<5} {band:<12} {hrs:<8} "
            f"{s['n_observations']:>5} {s['n_unique_brackets']:>5} "
            f"{s['hit_rate']:>7.1%} {s['mid_band_center']:>7.1%} {cost:>7}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scan_report.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add scan_report.py tests/test_scan_report.py
git commit -m "feat(scanner): price reliability curve with correlated-sample counts"
```

---

### Task 6: CLI entry point and scheduled workflow

**Files:**
- Modify: `scanner.py` (append `main()` and the `__main__` guard)
- Create: `.github/workflows/scan.yml`
- Test: `tests/test_scanner_cli.py` (create)

**Interfaces:**
- Consumes: `snapshot_pass`, `settlement_pass`, `_real_deps` (Tasks 3-4); `scan_report.reliability`, `format_table` (Task 5)
- Produces: `main(argv: list, deps=None, now=None) -> int` — exit code, `0` on success, `2` on unknown command

- [ ] **Step 1: Write the failing test**

Create `tests/test_scanner_cli.py`:

```python
from datetime import datetime, timezone

import scanner

_NOW = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)


def _deps(sink):
    return scanner.Deps(
        list_series=lambda: [{"ticker": "KXHIGHDEN", "title": "Denver"}],
        list_markets=lambda s, status=None: [
            {"ticker": "KXHIGHDEN-26AUG03-B72.5", "status": "active",
             "floor_strike": 72, "cap_strike": 73, "yes_bid": 0.33,
             "yes_ask": 0.37, "volume": 5,
             "close_time": "2026-08-04T06:00:00Z"}],
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scanner_cli.py -v`
Expected: FAIL — `AttributeError: module 'scanner' has no attribute 'main'`

- [ ] **Step 3: Write minimal implementation**

Append to `scanner.py`:

```python
def main(argv: list, deps: Deps = None, now: datetime = None) -> int:
    """`python scanner.py snapshot` / `python scanner.py settle`.

    Returns an exit code so a bad invocation fails the Action loudly instead of
    silently recording nothing."""
    deps = deps or _real_deps()
    now = now or datetime.now(timezone.utc)
    command = argv[0] if argv else ""
    if command == "snapshot":
        print(f"[scan] snapshot {snapshot_pass(now, deps)}")
        return 0
    if command == "settle":
        print(f"[scan] settle {settlement_pass(now, deps)}")
        return 0
    if command == "report":
        import scan_report
        stats = scan_report.reliability(
            deps.load_rows(scan_log.SNAPSHOT_PATH),
            deps.load_rows(scan_log.SETTLED_PATH))
        print(scan_report.format_table(stats))
        return 0
    print("usage: scanner.py {snapshot|settle|report}")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scanner_cli.py -v`
Expected: 4 passed

Then the full suite:
Run: `python3 -m pytest`
Expected: 988 passed (950 baseline + 9 + 9 + 4 + 4 + 8 + 4)

- [ ] **Step 5: Create the workflow**

Create `.github/workflows/scan.yml`:

```yaml
name: Kalshi multi-city price scan

# Read-only measurement, entirely separate from log.yml and trade.yml. Snapshots
# every Kalshi city temperature bracket's price three times a day and records
# Kalshi's own settlement result once a day. Places no orders and touches no
# trade state.
#
# ONE-TIME SETUP (before first run):
#   1. Create the data branch:
#        git switch --orphan scan-data && git commit --allow-empty -m init \
#          && git push origin scan-data && git switch -
#      (the contents API needs the branch to exist before the first write).
#   2. Secret: SCAN_GH_TOKEN — repo-scoped PAT with contents:write.
#      Kalshi's series/markets endpoints are public, so no Kalshi key is needed.
#
# Firings land ~18h, ~12h and ~6h before the ~06:00Z close of US city markets.
# `hours_to_close` is recorded per row from each market's own close_time, so a
# throttled or late run is harmless — it is not assumed from the schedule.
on:
  schedule:
    - cron: "0 12 * * *"
    - cron: "0 18 * * *"
    - cron: "0 0 * * *"
  workflow_dispatch:
    inputs:
      command:
        description: "snapshot | settle | report"
        required: true
        default: "snapshot"

permissions:
  contents: read

concurrency:
  group: kalshi-scan
  cancel-in-progress: false      # never drop a snapshot; they are not idempotent

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -r requirements.txt

      - name: Snapshot prices
        if: github.event_name == 'schedule'
        env:
          SCAN_GH_REPO: ${{ github.repository }}
          SCAN_GH_BRANCH: scan-data
          SCAN_GH_TOKEN: ${{ secrets.SCAN_GH_TOKEN }}
        run: python scanner.py snapshot

      # Settlement runs only on the 12:00Z firing: US city markets close ~06:00Z,
      # so by noon the prior climate day has finalized.
      - name: Record settlements
        if: github.event_name == 'schedule' && github.event.schedule == '0 12 * * *'
        env:
          SCAN_GH_REPO: ${{ github.repository }}
          SCAN_GH_BRANCH: scan-data
          SCAN_GH_TOKEN: ${{ secrets.SCAN_GH_TOKEN }}
        run: python scanner.py settle

      - name: Manual run
        if: github.event_name == 'workflow_dispatch'
        env:
          SCAN_GH_REPO: ${{ github.repository }}
          SCAN_GH_BRANCH: scan-data
          SCAN_GH_TOKEN: ${{ secrets.SCAN_GH_TOKEN }}
        run: python scanner.py ${{ inputs.command }}
```

- [ ] **Step 6: Commit**

```bash
git add scanner.py tests/test_scanner_cli.py .github/workflows/scan.yml
git commit -m "feat(scanner): CLI entry point and thrice-daily scan workflow"
```

---

## Corrections found during execution (2026-08-03)

Live verification exposed three things this plan got wrong. All fixed in
`3fc9ff1`; recorded here so the plan is not left misleading.

1. **Rate limiting was not planned for.** A 51-series pass fired back-to-back
   loses ~80% of series to HTTP 429. `Deps` gained a `sleep` and
   `REQUEST_SPACING_S = 0.5`; a full pass takes ~31s.
2. **The market payload shape in every fixture was invented, not observed.**
   Real fields are `yes_bid_dollars` / `yes_ask_dollars` (dollar *strings*) and
   `volume_fp`; `yes_bid`/`yes_ask`/`volume` do not exist. `strike_type` was
   also omitted from `build_snapshot_row` despite the spec listing it.
3. **`status="open"` is required on the snapshot fetch**, or the endpoint also
   returns every past day's settled markets.

**Corrected volume:** ~480 rows and ~135 KB per firing → ~1,440 rows/day,
~12 MB/month. The spec's ~6 MB estimate was 2x low.

## Deviations from the spec

Two, both deliberate — flag them if you disagree rather than silently
implementing something else:

1. **City display names are not resolved.** The spec said titles would be looked
   up at report time from the live series endpoint. The plan drops that: the
   report keys on the series ticker (`KXHIGHDEN`, `KXLOWTAUS`), which is already
   unambiguous, and resolving titles would put a network call inside a pure
   analysis function for cosmetics. `list_weather_series` still returns `title`,
   so this is easy to add later if the tables read badly.

2. **Prices outside 0.05–0.95 are excluded from the curve.** `PRICE_BANDS` spans
   only that range, and `_bucket` returns `None` outside it, so those rows are
   dropped. Near-0 and near-1 brackets are mostly already-resolved markets whose
   "calibration" is trivially perfect and would flatter every city's numbers.

## Post-implementation manual steps

These cannot be done by the implementing engineer and must be handed back:

1. **Create the branch:** `git switch --orphan scan-data && git commit --allow-empty -m init && git push origin scan-data && git switch -`
2. **Add the `SCAN_GH_TOKEN` secret** (repo-scoped PAT, `contents:write`).
3. **Trigger one manual `snapshot`** via workflow_dispatch and confirm rows appear on `scan-data`.
4. **Wait ~2 weeks**, then run the `report` command and read the curve.

## What the result means

If low-priced bands settle materially less often than their price implies —
consistently, across cities, and with the gap surviving `mean_no_cost` — the
favorite-longshot bias is real in these markets and a fade strategy is worth
building. The cities with the widest gap are where a per-city model should be
built first.

If the curve sits on the diagonal, the markets are efficient and the fade thesis
is dead. That is a cheap, valuable answer: it saves building ~20 city models.

Neither outcome yields a strategy on its own. Identifying *which* bracket is
overpriced on a given day still needs a per-city model — this only says whether
that work is worth doing, and where.
