# Same-Day Screen Row Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace four model alerts with a single push fired within ~5 minutes of a bracket settling *today* newly appearing on the Screen table.

**Architecture:** The 30-minute screen pass publishes a small `screen_reference.json` (forecast extreme, station, timezone per city). A new fast loop, `screen_alert.py`, reads it, polls Kalshi prices and NWS observations itself, applies the *existing* screen rules, filters to the in-progress climate day and the page's live-NO band, and pushes tickers it has not pushed before. It is dispatched by `log.yml`'s reliable 10-minute cron and does two checks 5 minutes apart per run.

**Tech Stack:** Python 3.9 locally / 3.11 on Actions, pytest, GitHub Actions, ntfy, Kalshi public REST, NWS API.

**Spec:** `docs/superpowers/specs/2026-08-07-screen-row-alert-design.md`

## Global Constraints

- Python 3.9 locally — every module starts `from __future__ import annotations`; keep doing that.
- Run tests with `python3 -m pytest` (no bare `python` on this machine).
- `screen_alert.py` must **never** import `screen_view` (it imports Streamlit) and must **never** write `scan_candidates.jsonl` — `screen_score`'s measurement record stays comparable.
- Do not change any screening threshold: `MIN_CANDIDATE_PRICE`, `MIN_CANDIDATE_GAP_F`, `SETTLED_PRICE`, `MIN_OBS_SUPPORT` are untouched.
- Morning Recap must keep working, including its `Austin: ` title prefix for non-default stations.
- All network work goes through an injectable `Deps` dataclass, matching `screen.Deps`, so tests never touch the network.
- Comments explain *why*, matching surrounding density. No decorative comments.
- Commit after each task with a Conventional Commits subject.

---

### Task 1: Move the live-price band into `screen_rules`

`screen_alert` needs the same NO-price band and parser the Screen page uses, and cannot import `screen_view`. Two copies of a band would silently drift.

**Files:**
- Modify: `scan_log.py:122-131` (`_dollars`)
- Modify: `screen_rules.py` (add band constants, `no_ask_of`, `within_band`)
- Modify: `screen_view.py:48,58,160-171` (delete the copies, re-export)
- Test: `tests/test_screen_rules_band.py` (new)

**Interfaces:**
- Produces: `scan_log.dollars(value)`; `screen_rules.MIN_LIVE_NO_PRICE = 0.20`, `screen_rules.MAX_LIVE_NO_PRICE = 0.90`, `screen_rules.no_ask_of(market: dict) -> float | None`, `screen_rules.within_band(price) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_screen_rules_band.py`:

```python
"""The live NO-price band — shared by the Screen page and the alert loop."""
import screen_rules
import screen_view


def test_no_ask_prefers_kalshis_own_no_ask():
    assert screen_rules.no_ask_of({"no_ask_dollars": "0.3500"}) == 0.35


def test_no_ask_falls_back_to_the_inverted_yes_bid():
    # Buying NO sells against the resting YES bid, so NO ask = 1 - yes bid.
    assert screen_rules.no_ask_of({"yes_bid_dollars": "0.8800"}) == 0.12


def test_no_ask_is_none_when_unquoted():
    assert screen_rules.no_ask_of({}) is None


def test_band_edges_are_inclusive():
    assert screen_rules.within_band(0.20) is True
    assert screen_rules.within_band(0.90) is True
    assert screen_rules.within_band(0.19) is False
    assert screen_rules.within_band(0.91) is False


def test_an_unquoted_row_survives_the_band():
    # Matches the page: an absent quote is thin liquidity, not a verdict about
    # the fade, so it must not hide the row.
    assert screen_rules.within_band(None) is True


def test_screen_view_shares_the_one_definition():
    assert screen_view.MIN_LIVE_NO_PRICE is screen_rules.MIN_LIVE_NO_PRICE
    assert screen_view.MAX_LIVE_NO_PRICE is screen_rules.MAX_LIVE_NO_PRICE
    assert screen_view.no_ask_of is screen_rules.no_ask_of
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_screen_rules_band.py -q`
Expected: FAIL — `AttributeError: module 'screen_rules' has no attribute 'no_ask_of'`.

- [ ] **Step 3: Make `_dollars` public in `scan_log.py`**

Rename the function and keep the old private name as an alias so the existing internal call sites keep working:

```python
def dollars(value):
```

Then immediately after the function body, add:

```python
_dollars = dollars   # pre-existing internal name; keep both pointing at one function
```

- [ ] **Step 4: Add the band to `screen_rules.py`**

Add `import scan_log` under the existing `import math` (no cycle: `scan_log` imports `sources.kalshi`, never `screen_*`). Then append at the end of the pricing section, after `price_of`:

```python
# What the Screen page and the alert loop both consider actionable RIGHT NOW.
# Below the floor the market has already resolved the bracket against the fade;
# above the cap it agrees with the fade and there is nothing left to win. Both
# consumers read these from here: two copies of a band silently drift.
MIN_LIVE_NO_PRICE = 0.20
MAX_LIVE_NO_PRICE = 0.90


def no_ask_of(market: dict):
    """Dollars to BUY NO on this market right now, or None when unquoted.

    Kalshi's own NO ask when there is one, else the YES bid inverted: buying NO
    sells against the resting YES bid, so NO ask = 1 - yes bid. Prices arrive as
    dollar STRINGS ("0.8800"), the gotcha that silently empties a scan pass."""
    ask = scan_log.dollars(market.get("no_ask_dollars"))
    if ask is not None:
        return ask
    bid = scan_log.dollars(market.get("yes_bid_dollars"))
    return None if bid is None else round(1.0 - bid, 2)


def within_band(price) -> bool:
    """Whether a live NO price is worth showing or pushing.

    An unquoted row (None) SURVIVES: an absent quote is thin liquidity or a
    market that has since closed, not evidence about the fade."""
    if price is None:
        return True
    return MIN_LIVE_NO_PRICE <= float(price) <= MAX_LIVE_NO_PRICE
```

- [ ] **Step 5: Re-export from `screen_view.py`**

Delete `screen_view`'s `MIN_LIVE_NO_PRICE` and `MAX_LIVE_NO_PRICE` assignments and its whole `no_ask_of` function, keeping their explanatory comments with the definitions that moved. Add beside the other imports:

```python
from screen_rules import (MAX_LIVE_NO_PRICE, MIN_LIVE_NO_PRICE,  # noqa: F401
                          no_ask_of)
```

Leave `tradeable_now` and `hidden_notice` unchanged — they reference the names, which now resolve to the imported ones.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_rules_band.py tests/test_screen_view.py tests/test_screen_rules_dead.py tests/test_screen_rules_forecast.py tests/test_scan_log.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add scan_log.py screen_rules.py screen_view.py tests/test_screen_rules_band.py
git commit -m "refactor(screen): move the live NO-price band into screen_rules"
```

---

### Task 2: Remove the four retired alerts

**Files:**
- Modify: `scheduled_log.py:31-32` (state paths), `:40` (`_state_path`), `:111` + `:127-158` (CLI alert), `:161-200` (resolved alert), `:228` (call site), `:37` (`RESOLVED_ALERT_PCT`)
- Modify: `alerts.py:1-7` (docstring), `:59-76` (`storm_body`/`front_body`), `:155-167` (triggers)
- Delete: `tests/test_cli_alert.py`, `tests/test_resolved_alert.py`
- Modify: `tests/test_alerts_events.py`, `tests/test_station_scheduled_log.py:38-39,55-80`

**Interfaces:**
- Produces: `alerts.maybe_fire_events(snap, now, station)` still exists and still fires only the Morning Recap.

- [ ] **Step 1: Replace `tests/test_alerts_events.py` with the recap-only suite**

```python
"""alerts.maybe_fire_events — the Morning Recap, the only event alert left."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
import alerts

_TZ = ZoneInfo(TIMEZONE)


def _snap(level="clear", front=False, sigma=3.0):
    return {
        "storm": {"level": level, "sigma": sigma,
                  "upstream": {"active": level == "active",
                               "county": "Tarrant", "direction": "NW"}},
        "today": {"low": {"consensus": 80.0, "front_widened": front,
                          "front_guard": {"projection": 77.0}}},
    }


def _patch(monkeypatch, tmp_path, sends, recap="Morning digest"):
    monkeypatch.setattr(alerts, "EVENT_STATE_PATH", str(tmp_path / "ev.json"))
    monkeypatch.setattr(alerts, "_build_recap_body", lambda snap: recap)
    monkeypatch.setattr(alerts.notify, "send_ntfy",
                        lambda title, body: sends.append((title, body)) or True)


# 3 PM local — past the recap window, so recap fires unless gated out.
_PM = datetime(2026, 7, 21, 15, 0, tzinfo=_TZ)


def test_recap_time_gate_and_once_per_day(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    before = datetime(2026, 7, 21, 6, 0, tzinfo=_TZ)
    alerts.maybe_fire_events(_snap(), before)          # 06:00 — too early
    assert not any(t == "Morning Recap" for t, _ in sends)
    at = datetime(2026, 7, 21, 6, 30, tzinfo=_TZ)
    alerts.maybe_fire_events(_snap(), at)              # 06:30 — fires
    alerts.maybe_fire_events(_snap(), _PM)             # later same day — quiet
    assert [t for t, _ in sends].count("Morning Recap") == 1
    tomorrow = datetime(2026, 7, 22, 6, 35, tzinfo=_TZ)
    alerts.maybe_fire_events(_snap(), tomorrow)        # re-arms
    assert [t for t, _ in sends].count("Morning Recap") == 2


def test_storm_and_front_no_longer_push(monkeypatch, tmp_path):
    # Retired 2026-08-07 in favour of the Screen row alert. An active storm and
    # a widened front together must produce the recap and nothing else.
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    alerts.maybe_fire_events(_snap(level="active", front=True), _PM)
    assert [t for t, _ in sends] == ["Morning Recap"]


def test_empty_state_file_does_not_block(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    (tmp_path / "ev.json").write_text("")
    alerts.maybe_fire_events(_snap(), _PM)
    assert [t for t, _ in sends] == ["Morning Recap"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_alerts_events.py -q`
Expected: FAIL on `test_storm_and_front_no_longer_push` — three titles are sent.

- [ ] **Step 3: Strip `alerts.py`**

Change the module docstring's first line to:

```python
"""ntfy event alerts fired from the scheduled run: the Morning Recap digest.

Pure message-builders + state I/O live here (unit-testable, no network/Streamlit);
`maybe_fire_events` orchestrates the once-per-day send. Kept cron-safe — no
Streamlit import at module top.

Storm Watch and Front Risk were retired 2026-08-07: the phone's alert budget now
goes to screen_alert, which pushes new same-day Screen rows.
"""
```

Delete `storm_body` and `front_body` entirely, and delete both of their `try:` blocks from `maybe_fire_events` (the storm block and the front block), leaving the `cday` computation, `_send`, the recap block and the state save. `cday` is now unused by any remaining branch — delete it and its `try/except` too, since the recap keys off `local.date()`.

- [ ] **Step 4: Strip `scheduled_log.py`**

Delete: the `RESOLVED_ALERT_PCT` constant, the `STATE_PATH` and `RESOLVED_STATE_PATH` constants, the `_state_path` helper, the whole `_maybe_alert_cli` function, the whole `_maybe_alert_resolved` function, the `_maybe_alert_resolved(cli_snap, now, station)` call inside `_log_snapshots`, and the `_maybe_alert_cli(datetime.now(TZ), code)` call near line 228.

Then run `python3 -c "import ast,sys; ast.parse(open('scheduled_log.py').read())"` and check whether `os`, `json` or `settlement` are still referenced (`grep -n "os\.\|json\.\|settlement\." scheduled_log.py`); remove any import that no longer is.

- [ ] **Step 5: Fix the two station tests**

In `tests/test_station_scheduled_log.py`, delete these two lines:

```python
    monkeypatch.setattr(scheduled_log, "_maybe_alert_cli", lambda *a, **k: None)
    monkeypatch.setattr(scheduled_log, "_maybe_alert_resolved", lambda *a, **k: None)
```

and replace `test_alerts_are_station_tagged_for_non_default` wholesale with:

```python
def test_alerts_are_station_tagged_for_non_default(monkeypatch, tmp_path):
    """Austin's recap gets a name-prefixed title and its own state file;
    Dallas (default) titles stay unprefixed (byte-identical)."""
    import alerts
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sent = []
    monkeypatch.setattr(alerts.notify, "send_ntfy",
                        lambda title, body: sent.append(title) or True)
    monkeypatch.setattr(alerts, "event_state_path",
                        lambda station=config.DEFAULT_STATION: str(tmp_path / f"ev_{station}.json"))
    monkeypatch.setattr(alerts, "_build_recap_body", lambda snap: "digest")
    now = datetime(2026, 7, 26, 13, 0, tzinfo=ZoneInfo("America/Chicago"))

    alerts.maybe_fire_events({}, now, station="KAUS")
    alerts.maybe_fire_events({}, now, station="KDFW")
    assert "Austin: Morning Recap" in sent
    assert "Morning Recap" in sent          # Dallas unprefixed
```

- [ ] **Step 6: Delete the two retired test files**

```bash
git rm tests/test_cli_alert.py tests/test_resolved_alert.py
```

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass. If anything still references `RESOLVED_ALERT_PCT`, `_maybe_alert_cli` or `_maybe_alert_resolved`, remove that reference — nothing outside the deleted code should use them (`grep -rn "RESOLVED_ALERT_PCT\|_maybe_alert" --include="*.py" .`).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(alerts): retire the CLI, resolved, storm and front pushes"
```

---

### Task 3: Whole-document read/write on the scan-data branch

The candidate log is append-only JSONL partitions. The reference and the alert state are single small documents that get *replaced*, so they need their own pair rather than being forced through `append_many`.

**Files:**
- Modify: `scan_log.py` (add constants + two functions after `load_recent`)
- Test: `tests/test_scan_log_docs.py` (new)

**Interfaces:**
- Produces: `scan_log.REFERENCE_PATH = "screen_reference.json"`, `scan_log.ALERT_STATE_PATH = "screen_alert_state.json"`, `scan_log.read_doc(path, transport=None) -> dict`, `scan_log.write_doc(path, obj, transport=None) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scan_log_docs.py`:

```python
"""Whole-file JSON documents on the scan-data branch (reference + alert state)."""
import json

import scan_log


class FakeTransport:
    def __init__(self, text=None, sha="sha1"):
        self.store = None if text is None else (text, sha)
        self.puts = []

    def get(self, path):
        return self.store

    def put(self, path, text, sha):
        self.puts.append((path, text, sha))
        self.store = (text, "sha2")


def test_read_doc_parses_the_document():
    t = FakeTransport(json.dumps({"generated": "2026-08-07T18:30:00Z"}))
    assert scan_log.read_doc("x.json", t)["generated"] == "2026-08-07T18:30:00Z"


def test_read_doc_is_empty_when_absent():
    assert scan_log.read_doc("x.json", FakeTransport(None)) == {}


def test_read_doc_survives_corrupt_content():
    # A half-written document must read as "nothing yet", never raise: this runs
    # in a cron whose whole job is to be quietly reliable.
    assert scan_log.read_doc("x.json", FakeTransport("{not json")) == {}


def test_read_doc_rejects_a_non_object():
    assert scan_log.read_doc("x.json", FakeTransport("[1, 2]")) == {}


def test_write_doc_replaces_and_carries_the_sha():
    t = FakeTransport(json.dumps({"a": 1}))
    scan_log.write_doc("x.json", {"b": 2}, t)
    path, text, sha = t.puts[0]
    assert path == "x.json"
    assert json.loads(text) == {"b": 2}
    assert sha == "sha1"          # the contents API needs the prior sha


def test_write_doc_creates_a_missing_file():
    t = FakeTransport(None)
    scan_log.write_doc("x.json", {"b": 2}, t)
    assert t.puts[0][2] is None


def test_the_document_paths_are_named():
    assert scan_log.REFERENCE_PATH == "screen_reference.json"
    assert scan_log.ALERT_STATE_PATH == "screen_alert_state.json"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_scan_log_docs.py -q`
Expected: FAIL — `AttributeError: module 'scan_log' has no attribute 'read_doc'`.

- [ ] **Step 3: Implement in `scan_log.py`**

Add beside the existing path constants:

```python
REFERENCE_PATH = "screen_reference.json"      # screen.py -> screen_alert.py
ALERT_STATE_PATH = "screen_alert_state.json"  # tickers already pushed, by day
```

and after `load_recent`:

```python
def read_doc(path: str, transport=None) -> dict:
    """A whole-file JSON document, or {} when absent, corrupt or not an object.

    The snapshot and candidate logs are append-only JSONL partitions; the
    screen's reference and the alerter's state are single small documents that
    are REPLACED on every write, so they get their own pair. Never raises: both
    readers run in a cron whose job is to stay quietly reliable, and a
    half-written document must read as "nothing yet"."""
    got = _t(transport).get(path)
    if not got:
        return {}
    try:
        obj = json.loads(got[0])
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def write_doc(path: str, obj: dict, transport=None) -> None:
    """Replace `path` with `obj`. One GET (for the sha) plus one PUT."""
    t = _t(transport)
    got = t.get(path)
    t.put(path, json.dumps(obj), got[1] if got else None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_scan_log_docs.py tests/test_scan_log.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scan_log.py tests/test_scan_log_docs.py
git commit -m "feat(scan): read and write whole-document JSON on the data branch"
```

---

### Task 4: `screen.py` publishes the reference

**Files:**
- Modify: `screen.py:26-58` (`Deps`, `_real_deps`), `:87-179` (`screen_pass`)
- Test: `tests/test_screen_reference.py` (new)

**Interfaces:**
- Consumes: `scan_log.write_doc`, `scan_log.REFERENCE_PATH` (Task 3).
- Produces: `screen.Deps.write_reference: Callable[[dict], None]`; the published document `{"generated": iso, "cities": {series: {"station", "timezone", "days": {iso_date: extreme_or_null}}}}`. Also renames `screen._observed_readings` → `screen.observed_readings` and `screen._real_fetch_obs` → `screen.fetch_observations`, both public for `screen_alert`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_screen_reference.py`:

```python
"""screen_pass publishes the small reference the fast alert loop reads."""
from datetime import datetime, timezone

import screen

_NOW = datetime(2026, 8, 7, 18, 30, tzinfo=timezone.utc)

_PERIODS = [
    {"startTime": "2026-08-07T06:00:00-06:00", "temperature": 61,
     "probabilityOfPrecipitation": {"value": 0}, "shortForecast": "Clear"},
    {"startTime": "2026-08-07T15:00:00-06:00", "temperature": 94,
     "probabilityOfPrecipitation": {"value": 0}, "shortForecast": "Sunny"},
]


def _market(ticker):
    return {"ticker": ticker, "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.35",
            "yes_sub_title": "72° or above", "floor_strike": 71, "cap_strike": None,
            "strike_type": "greater", "volume_fp": "100",
            "close_time": "2026-08-08T05:59:00Z"}


def _deps(published):
    return screen.Deps(
        list_series=lambda: [{"ticker": "KXLOWTDEN"}],
        list_markets=lambda series, status=None: [_market("KXLOWTDEN-26AUG07-T71")],
        resolve_point=lambda lat, lon: {
            "timezone": "America/Denver",
            "forecast_hourly": "https://example.test/hourly",
            "stations_url": "https://example.test/stations"},
        fetch_forecast=lambda url: _PERIODS,
        fetch_obs=lambda station, start, end: [],
        append_rows=lambda path, rows: len(rows),
        station_for=lambda url: "KDEN",
        sleep=lambda s: None,
        write_reference=lambda obj: published.append(obj),
    )


def test_reference_carries_station_timezone_and_extreme():
    published = []
    screen.screen_pass(_NOW, _deps(published))
    city = published[0]["cities"]["KXLOWTDEN"]
    assert city["station"] == "KDEN"
    assert city["timezone"] == "America/Denver"
    # The LOW series publishes the day's low, unfolded by realized temperature —
    # the alerter re-folds it against its own fresher observations.
    assert city["days"]["2026-08-07"] == 61.0


def test_reference_is_stamped_with_the_pass_time():
    published = []
    screen.screen_pass(_NOW, _deps(published))
    assert published[0]["generated"] == "2026-08-07T18:30:00Z"


def test_a_failed_reference_write_does_not_lose_the_candidates():
    # Candidates are the product; the reference is a convenience for the alerter.
    def boom(obj):
        raise RuntimeError("contents API down")

    deps = _deps([])
    deps.write_reference = boom
    got = screen.screen_pass(_NOW, deps)
    assert got["cities"] == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_screen_reference.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'write_reference'`.

- [ ] **Step 3: Rename the two observation helpers to public names**

In `screen.py`, rename `_real_fetch_obs` → `fetch_observations` and `_observed_readings` → `observed_readings`, updating the two internal references (`_real_deps`'s `fetch_obs=` and the call inside `screen_pass`). Add to each docstring's end:

```python
    Public because screen_alert reuses it on its 5-minute loop.
```

- [ ] **Step 4: Add `write_reference` to `Deps` and `_real_deps`**

```python
@dataclass
class Deps:
    list_series: Callable
    list_markets: Callable
    resolve_point: Callable
    fetch_forecast: Callable
    fetch_obs: Callable
    append_rows: Callable
    station_for: Callable
    sleep: Callable = time.sleep
    write_reference: Callable = None
```

and in `_real_deps`, after `station_for=...`:

```python
        write_reference=lambda obj: scan_log.write_doc(scan_log.REFERENCE_PATH, obj),
```

- [ ] **Step 5: Accumulate and publish the reference in `screen_pass`**

Initialise beside the other accumulators:

```python
    candidates, cities, errors = [], 0, 0
    reference: dict = {}
```

Hoist the station lookup out of the per-day loop so the reference always carries it, even on a day that is not in progress. Replace the `station`/`features` lines inside `if in_progress:` with a single lookup right after `cities += 1`:

```python
        try:
            station = deps.station_for(resolved.get("stations_url"))
        except Exception as e:            # noqa: BLE001 - degrade to forecast only
            print(f"[screen] {series}: station lookup skipped ({e})")
            station = None
        reference[series] = {"station": station, "timezone": tzname, "days": {}}
```

and inside `if in_progress:` keep only:

```python
                try:
                    features = deps.fetch_obs(station, start, now) if station else []
                    readings = observed_readings(features, tzname, day)
                except Exception as e:    # noqa: BLE001 - degrade to forecast
                    print(f"[screen] {series}: observations skipped ({e})")
```

Record the extreme just after `extremes = screen_forecast.daily_extremes(...)`:

```python
            # The UNFOLDED extreme: screen_alert re-folds it against its own,
            # fresher observations, so folding here would bake in this pass's.
            reference[series]["days"][day.isoformat()] = extremes.get(variable)
```

Finally, after the existing `written = deps.append_rows(...)` line:

```python
    # Best-effort and last: the candidate log is the product, and a contents-API
    # failure here must not cost the pass its rows.
    if deps.write_reference:
        try:
            deps.write_reference({"generated": now_iso, "cities": reference})
        except Exception as e:            # noqa: BLE001
            print(f"[screen] reference publish failed ({e})")
    return {"candidates": written or 0, "cities": cities, "errors": errors}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_reference.py tests/test_screen_pass.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add screen.py tests/test_screen_reference.py
git commit -m "feat(screen): publish the reference the fast alert loop reads"
```

---

### Task 5: `screen_alert` selection logic

**Files:**
- Create: `screen_alert.py`
- Test: `tests/test_screen_alert_select.py` (new)

**Interfaces:**
- Consumes: `screen_rules.{dead_candidate, forecast_candidate, realized_extreme, no_ask_of, within_band}`, `screen_forecast.{climate_day_of_ticker, lst_offset_hours, fold_realized}`, `scan_log.{build_snapshot_row, variable_of_series}`.
- Produces: `screen_alert.STALE_AFTER_MIN = 90`; `reference_age_minutes(reference, now) -> float | None`; `forecast_is_usable(reference, now) -> bool`; `in_progress_day(now, tzname) -> date`; `day_window(day, tzname) -> (datetime, datetime)`; `city_candidates(series, day, markets, realized, now, forecast_extreme) -> list[dict]`. Candidate dicts are `screen_rules._candidate` shape plus a `no_price` key.

- [ ] **Step 1: Write the failing test**

Create `tests/test_screen_alert_select.py`:

```python
"""screen_alert's selection: same-day only, band-filtered, dead beats forecast."""
from datetime import date, datetime, timezone

import screen_alert

_NOW = datetime(2026, 8, 7, 18, 30, tzinfo=timezone.utc)
_DAY = date(2026, 8, 7)


def _market(ticker, yes_ask="0.35", no_ask=None):
    """A Denver low bracket paying at 72°F and above ('greater', floor 71)."""
    m = {"ticker": ticker, "yes_bid_dollars": "0.30", "yes_ask_dollars": yes_ask,
         "yes_sub_title": "72° or above", "floor_strike": 71, "cap_strike": None,
         "strike_type": "greater", "volume_fp": "100",
         "close_time": "2026-08-08T05:59:00Z"}
    if no_ask is not None:
        m["no_ask_dollars"] = no_ask
    return m


def _call(markets, realized=(), forecast_extreme=None):
    return screen_alert.city_candidates(
        "KXLOWTDEN", _DAY, markets, list(realized), _NOW, forecast_extreme)


def test_a_forecast_gap_becomes_a_candidate():
    got = _call([_market("KXLOWTDEN-26AUG07-T71")], forecast_extreme=61.0)
    assert [c["kind"] for c in got] == ["forecast"]
    assert got[0]["ticker"] == "KXLOWTDEN-26AUG07-T71"


def test_tomorrows_bracket_is_never_a_candidate():
    # The whole point: brackets closing the next day are not interesting.
    assert _call([_market("KXLOWTDEN-26AUG08-T71")], forecast_extreme=61.0) == []


def test_realized_temperature_makes_it_dead():
    # Two readings of 61 clear MIN_OBS_SUPPORT, so the settled low can only fall
    # further and a 72-and-above bracket is already lost.
    got = _call([_market("KXLOWTDEN-26AUG07-T71")], realized=[61.0, 61.0])
    assert [c["kind"] for c in got] == ["dead"]


def test_dead_wins_when_both_screens_would_fire():
    got = _call([_market("KXLOWTDEN-26AUG07-T71")],
                realized=[61.0, 61.0], forecast_extreme=61.0)
    assert [c["kind"] for c in got] == ["dead"]


def test_no_forecast_extreme_leaves_only_the_dead_screen():
    # This is the stale-reference path: dead needs observations alone.
    assert _call([_market("KXLOWTDEN-26AUG07-T71")], forecast_extreme=None) == []
    got = _call([_market("KXLOWTDEN-26AUG07-T71")],
                realized=[61.0, 61.0], forecast_extreme=None)
    assert [c["kind"] for c in got] == ["dead"]


def test_the_live_no_band_is_applied():
    cheap = _call([_market("KXLOWTDEN-26AUG07-T71", no_ask="0.19")],
                  forecast_extreme=61.0)
    dear = _call([_market("KXLOWTDEN-26AUG07-T71", no_ask="0.91")],
                 forecast_extreme=61.0)
    inside = _call([_market("KXLOWTDEN-26AUG07-T71", no_ask="0.20")],
                   forecast_extreme=61.0)
    assert cheap == [] and dear == []
    assert [c["no_price"] for c in inside] == [0.20]


def test_an_unquoted_market_is_dropped_before_the_band():
    # build_snapshot_row returns None without any quote; nothing to screen.
    assert _call([{"ticker": "KXLOWTDEN-26AUG07-T71"}], forecast_extreme=61.0) == []


def test_in_progress_day_uses_fixed_standard_time():
    # 05:30Z on Aug 8 is 23:30 Mountain STANDARD time on Aug 7, so the Denver
    # climate day still running is the 7th.
    now = datetime(2026, 8, 8, 5, 30, tzinfo=timezone.utc)
    assert screen_alert.in_progress_day(now, "America/Denver") == date(2026, 8, 7)


def test_day_window_starts_at_local_standard_midnight():
    start, end = screen_alert.day_window(_DAY, "America/Denver")
    assert start.isoformat() == "2026-08-07T07:00:00+00:00"   # 00:00 MST
    assert (end - start).total_seconds() == 24 * 3600


def test_reference_age_and_freshness():
    ref = {"generated": "2026-08-07T18:00:00Z"}
    assert screen_alert.reference_age_minutes(ref, _NOW) == 30.0
    assert screen_alert.forecast_is_usable(ref, _NOW) is True
    stale = {"generated": "2026-08-07T16:00:00Z"}      # 150 min
    assert screen_alert.forecast_is_usable(stale, _NOW) is False
    assert screen_alert.forecast_is_usable({}, _NOW) is False
    assert screen_alert.reference_age_minutes({"generated": "nonsense"}, _NOW) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_screen_alert_select.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'screen_alert'`.

- [ ] **Step 3: Create `screen_alert.py` with the selection half**

```python
"""Fast alert loop: push when a bracket settling TODAY newly appears on the
Screen table.

Runs every ~5 minutes, so it must stay cheap. It does NOT recompute the NWS
forecast: screen.py publishes screen_reference.json every 30 minutes and this
re-folds that extreme against its own fresh observations. Prices come straight
from Kalshi, which is what actually moves a row into the table.

Read-only against scan_candidates.jsonl — nothing here writes the candidate log,
so screen_score's measurement record stays comparable. Never imports
screen_view, which imports Streamlit and cannot run in a cron.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import scan_log
import screen_forecast
import screen_rules

# Three missed 30-minute passes. Past this the forecast half of the reference is
# too old to call a gap news, so only the dead screen — which needs observations
# alone — may fire.
STALE_AFTER_MIN = 90


def reference_age_minutes(reference: dict, now: datetime):
    """Minutes since the reference was published, or None when unreadable."""
    stamp = (reference or {}).get("generated")
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - when).total_seconds() / 60.0


def forecast_is_usable(reference: dict, now: datetime) -> bool:
    """Whether the soft (forecast-gap) screen may fire on this reference."""
    age = reference_age_minutes(reference, now)
    return age is not None and age <= STALE_AFTER_MIN


def in_progress_day(now: datetime, tzname: str) -> date:
    """The climate day running right now in this city.

    Fixed LST, not local time: the climate day ends at 01:00 local during
    daylight saving, so the local date is a day ahead for that hour."""
    offset = screen_forecast.lst_offset_hours(tzname)
    return (now.astimezone(timezone.utc) + timedelta(hours=offset)).date()


def day_window(day: date, tzname: str):
    """(start, end) in UTC of a city's LST climate day."""
    offset = screen_forecast.lst_offset_hours(tzname)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) \
        - timedelta(hours=offset)
    return start, start + timedelta(days=1)


def city_candidates(series: str, day: date, markets: list, realized: list,
                    now: datetime, forecast_extreme) -> list:
    """Alertable rows for one city's in-progress climate day.

    `forecast_extreme` is None when the reference is stale or missing, which
    disables the soft screen; the hard one needs only `realized`. Dead wins when
    both would fire — it is the half that claims certainty, and saying "already
    impossible" is strictly more useful than "far from the forecast".

    The price tested here IS the live price, unlike the page's, which compares a
    firing price up to hours old against a separately fetched quote."""
    now_iso = now.isoformat().replace("+00:00", "Z")
    variable = scan_log.variable_of_series(series)
    bound = screen_rules.realized_extreme(realized, variable)
    forecast = (screen_forecast.fold_realized(forecast_extreme, realized, variable)
                if forecast_extreme is not None else None)
    out = []
    for market in markets or []:
        row = scan_log.build_snapshot_row(market, series, now)
        if row is None:                   # unquoted; nothing to screen
            continue
        if screen_forecast.climate_day_of_ticker(row["ticker"]) != day:
            continue
        no_price = screen_rules.no_ask_of(market)
        if not screen_rules.within_band(no_price):
            continue
        hit = screen_rules.dead_candidate(row, bound, now_iso)
        if hit is None and forecast is not None:
            hit = screen_rules.forecast_candidate(row, forecast, now_iso)
        if hit is None:
            continue
        hit["no_price"] = no_price
        out.append(hit)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_alert_select.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add screen_alert.py tests/test_screen_alert_select.py
git commit -m "feat(alerts): select new same-day screen rows"
```

---

### Task 6: State, message and the check orchestration

**Files:**
- Modify: `screen_alert.py`
- Test: `tests/test_screen_alert_check.py` (new)

**Interfaces:**
- Consumes: everything from Task 5, plus `scan_log.read_doc/write_doc/REFERENCE_PATH/ALERT_STATE_PATH` (Task 3) and `screen.observed_readings/fetch_observations` (Task 4).
- Produces: `screen_alert.Deps` (fields `read_reference, read_state, write_state, list_markets, fetch_obs, notify, sleep`); `unseen(candidates, state) -> list`; `record(state, candidates) -> dict`; `prune(state, today, keep_days=2) -> dict`; `alert_title(n) -> str`; `alert_body(candidates, max_lines=10) -> str`; `check(now, deps) -> dict`; `main(argv, deps=None, now=None) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_screen_alert_check.py`:

```python
"""screen_alert.check — dedupe, state hygiene, the push, and the stale guard."""
from datetime import date, datetime, timezone

import screen_alert

_NOW = datetime(2026, 8, 7, 18, 30, tzinfo=timezone.utc)

_REFERENCE = {
    "generated": "2026-08-07T18:20:00Z",
    "cities": {"KXLOWTDEN": {"station": "KDEN", "timezone": "America/Denver",
                             "days": {"2026-08-07": 61.0}}},
}


def _market(ticker="KXLOWTDEN-26AUG07-T71"):
    return {"ticker": ticker, "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.35",
            "yes_sub_title": "72° or above", "floor_strike": 71, "cap_strike": None,
            "strike_type": "greater", "volume_fp": "100",
            "close_time": "2026-08-08T05:59:00Z"}


def _obs(temp_c=16.1):
    return [{"properties": {"timestamp": "2026-08-07T12:00:00+00:00",
                            "temperature": {"value": temp_c}}},
            {"properties": {"timestamp": "2026-08-07T13:00:00+00:00",
                            "temperature": {"value": temp_c}}}]


class Harness:
    def __init__(self, reference=None, state=None, obs=None, ok=True):
        self.reference = _REFERENCE if reference is None else reference
        self.state = state or {}
        self.obs = obs or []
        self.ok = ok
        self.sent = []
        self.written = []

    def deps(self):
        return screen_alert.Deps(
            read_reference=lambda: self.reference,
            read_state=lambda: dict(self.state),
            write_state=lambda obj: self.written.append(obj),
            list_markets=lambda series: [_market()],
            fetch_obs=lambda station, start, end: self.obs,
            notify=lambda title, body: self.sent.append((title, body)) or self.ok,
            sleep=lambda s: None,
        )


def test_a_new_row_pushes_once_and_records_it():
    h = Harness()
    got = screen_alert.check(_NOW, h.deps())
    assert got["new"] == 1
    title, body = h.sent[0]
    assert title == "1 new screen row"
    assert "Denver" in body
    assert h.written[0]["2026-08-07"] == ["KXLOWTDEN-26AUG07-T71"]


def test_an_already_pushed_ticker_stays_quiet():
    h = Harness(state={"2026-08-07": ["KXLOWTDEN-26AUG07-T71"]})
    got = screen_alert.check(_NOW, h.deps())
    assert got["new"] == 0
    assert h.sent == []
    # A quiet check must not write: it would be a commit every five minutes.
    assert h.written == []


def test_state_is_not_advanced_when_the_push_fails():
    # Otherwise a failed ntfy POST loses the row forever.
    h = Harness(ok=False)
    screen_alert.check(_NOW, h.deps())
    assert h.written == []


def test_a_stale_reference_silences_the_forecast_screen():
    stale = dict(_REFERENCE, generated="2026-08-07T15:00:00Z")   # 210 min
    h = Harness(reference=stale)
    assert screen_alert.check(_NOW, h.deps())["new"] == 0
    assert h.sent == []


def test_a_stale_reference_still_allows_a_dead_row():
    stale = dict(_REFERENCE, generated="2026-08-07T15:00:00Z")
    h = Harness(reference=stale, obs=_obs())      # 16.1C = 61F, twice
    assert screen_alert.check(_NOW, h.deps())["new"] == 1
    assert "DEAD" in h.sent[0][1]


def test_a_missing_reference_checks_nothing_and_never_raises():
    h = Harness(reference={})
    assert screen_alert.check(_NOW, h.deps())["new"] == 0


def test_unseen_ignores_duplicate_tickers_in_one_pass():
    rows = [{"ticker": "KXLOWTDEN-26AUG07-T71"},
            {"ticker": "KXLOWTDEN-26AUG07-T71"}]
    assert len(screen_alert.unseen(rows, {})) == 1


def test_record_then_unseen_suppresses_a_re_entry():
    # A bracket oscillating around the 20% floor must not become a stream.
    rows = [{"ticker": "KXLOWTDEN-26AUG07-T71"}]
    state = screen_alert.record({}, rows)
    assert screen_alert.unseen(rows, state) == []


def test_prune_keeps_two_days():
    state = {"2026-08-04": ["a"], "2026-08-05": ["b"],
             "2026-08-06": ["c"], "2026-08-07": ["d"]}
    kept = screen_alert.prune(state, date(2026, 8, 7))
    assert sorted(kept) == ["2026-08-05", "2026-08-06", "2026-08-07"]


def test_prune_drops_an_unparseable_key():
    assert screen_alert.prune({"junk": ["a"]}, date(2026, 8, 7)) == {}


def test_alert_title_is_singular_for_one():
    assert screen_alert.alert_title(1) == "1 new screen row"
    assert screen_alert.alert_title(3) == "3 new screen rows"


def test_alert_body_lines_read_as_a_decision():
    forecast_row = {"series": "KXLOWTDEN", "variable": "low",
                    "label": "72° or above", "no_price": 0.35,
                    "forecast": 61.0, "gap": 11.0, "kind": "forecast"}
    dead_row = {"series": "KXHIGHMIA", "variable": "high",
                "label": "91° to 92°", "no_price": 0.22,
                "forecast": 94.0, "gap": 2.0, "kind": "dead"}
    body = screen_alert.alert_body([forecast_row, dead_row]).splitlines()
    assert body[0] == "Denver low 72° or above · NO 35% · Ref 61 (11° gap)"
    assert body[1] == "Miami high 91° to 92° · NO 22% · DEAD (max 94 already)"


def test_alert_body_caps_its_length():
    rows = [{"series": "KXLOWTDEN", "variable": "low", "label": f"{i}",
             "no_price": 0.5, "forecast": 61.0, "gap": 9.0, "kind": "forecast"}
            for i in range(14)]
    lines = screen_alert.alert_body(rows).splitlines()
    assert len(lines) == 11
    assert lines[-1] == "…and 4 more"


def test_alert_body_shows_an_unquoted_row_honestly():
    row = {"series": "KXLOWTDEN", "variable": "low", "label": "72° or above",
           "no_price": None, "forecast": 61.0, "gap": 11.0, "kind": "forecast"}
    assert "NO — ·" in screen_alert.alert_body([row])


def test_main_rejects_an_unknown_command():
    assert screen_alert.main([]) == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_screen_alert_check.py -q`
Expected: FAIL — `AttributeError: module 'screen_alert' has no attribute 'Deps'`.

- [ ] **Step 3: Add the state, message and orchestration halves to `screen_alert.py`**

Extend the imports:

```python
import time
from dataclasses import dataclass
from typing import Callable

import scan_cities
import scan_log
import screen
import screen_forecast
import screen_rules
```

and append:

```python
MAX_BODY_LINES = 10      # a notification longer than this is unreadable
STATE_KEEP_DAYS = 2
REQUEST_SPACING_S = 0.5  # the same Kalshi pacing the screen and scanner use


@dataclass
class Deps:
    read_reference: Callable
    read_state: Callable
    write_state: Callable
    list_markets: Callable
    fetch_obs: Callable
    notify: Callable
    sleep: Callable = time.sleep


def _real_deps() -> Deps:
    import notify as notify_mod
    from sources import kalshi
    return Deps(
        read_reference=lambda: scan_log.read_doc(scan_log.REFERENCE_PATH),
        read_state=lambda: scan_log.read_doc(scan_log.ALERT_STATE_PATH),
        write_state=lambda obj: scan_log.write_doc(scan_log.ALERT_STATE_PATH, obj),
        list_markets=lambda series: kalshi.list_series_markets(series, status="open"),
        fetch_obs=screen.fetch_observations,
        notify=notify_mod.send_ntfy,
    )


def _day_key(ticker: str):
    day = screen_forecast.climate_day_of_ticker(ticker)
    return None if day is None else day.isoformat()


def unseen(candidates: list, state: dict) -> list:
    """Candidates whose ticker has not been pushed for its own climate day.

    Deduplicates within the pass too: one bracket can be reached twice if a
    series ever lists it twice, and two notifications for one row is a bug the
    phone would show."""
    out, seen = [], set()
    for c in candidates:
        ticker = c.get("ticker")
        key = _day_key(ticker or "")
        if not ticker or key is None or ticker in seen:
            continue
        if ticker in set(state.get(key) or []):
            continue
        seen.add(ticker)
        out.append(c)
    return out


def record(state: dict, candidates: list) -> dict:
    """Mark these tickers as pushed, under their own climate day."""
    out = dict(state)
    for c in candidates:
        key = _day_key(c.get("ticker") or "")
        if key is None:
            continue
        out[key] = sorted(set(out.get(key) or []) | {c["ticker"]})
    return out


def prune(state: dict, today: date, keep_days: int = STATE_KEEP_DAYS) -> dict:
    """Drop days older than `keep_days`, and any key that is not a date.

    Kalshi temperature markets close within ~30h of listing, so two days is
    ample and keeps the file from growing without bound."""
    cutoff = today - timedelta(days=keep_days)
    out = {}
    for key, tickers in (state or {}).items():
        try:
            when = date.fromisoformat(str(key))
        except ValueError:
            continue
        if when >= cutoff:
            out[key] = tickers
    return out


def alert_title(count: int) -> str:
    return f"{count} new screen row" + ("" if count == 1 else "s")


def _line(c: dict) -> str:
    city = scan_cities.city_name(c.get("series"))
    label = c.get("label") or c.get("ticker")
    price = c.get("no_price")
    shown = "—" if price is None else f"{round(float(price) * 100)}%"
    reference = c.get("forecast")
    if c.get("kind") == "dead":
        word = "max" if c.get("variable") == "high" else "min"
        tail = f"DEAD ({word} {reference:g} already)"
    else:
        tail = f"Ref {reference:g} ({c.get('gap'):g}° gap)"
    return f"{city} {c.get('variable')} {label} · NO {shown} · {tail}"


def alert_body(candidates: list, max_lines: int = MAX_BODY_LINES) -> str:
    lines = [_line(c) for c in candidates[:max_lines]]
    extra = len(candidates) - len(lines)
    if extra > 0:
        lines.append(f"…and {extra} more")
    return "\n".join(lines)


def check(now: datetime, deps: Deps) -> dict:
    """One pass: price every mapped city's same-day ladder and push what is new."""
    reference = deps.read_reference() or {}
    usable = forecast_is_usable(reference, now)
    if not usable:
        age = reference_age_minutes(reference, now)
        print(f"[screen_alert] reference age {age}min — dead rows only"
              if age is not None else "[screen_alert] no reference — dead rows only")
    state = deps.read_state() or {}
    found, cities = [], 0
    for series, info in (reference.get("cities") or {}).items():
        tzname, station = info.get("timezone"), info.get("station")
        if not tzname:
            continue
        day = in_progress_day(now, tzname)
        try:
            markets = deps.list_markets(series)
            deps.sleep(REQUEST_SPACING_S)
        except Exception as e:            # noqa: BLE001 - one city must not
            print(f"[screen_alert] {series}: markets skipped ({e})")   # cost the rest
            continue
        cities += 1
        readings = []
        if station:
            start, _ = day_window(day, tzname)
            try:
                features = deps.fetch_obs(station, start, now)
                readings = screen.observed_readings(features, tzname, day)
            except Exception as e:        # noqa: BLE001 - degrade to forecast
                print(f"[screen_alert] {series}: observations skipped ({e})")
        extreme = (info.get("days") or {}).get(day.isoformat()) if usable else None
        found.extend(city_candidates(series, day, markets,
                                     [t for _, t in readings], now, extreme))
    fresh = unseen(found, state)
    if not fresh:
        return {"cities": cities, "found": len(found), "new": 0}
    if deps.notify(alert_title(len(fresh)), alert_body(fresh)):
        # Only after a delivered push: advancing state on a failed POST would
        # lose the row for good, and this is the alert's entire purpose.
        deps.write_state(prune(record(state, fresh), now.date()))
    else:
        print("[screen_alert] send_ntfy False — state not advanced")
    return {"cities": cities, "found": len(found), "new": len(fresh)}


def main(argv: list, deps: Deps = None, now: datetime = None) -> int:
    if (argv[0] if argv else "") == "check":
        deps = deps or _real_deps()
        print(f"[screen_alert] {check(now or datetime.now(timezone.utc), deps)}")
        return 0
    print("usage: screen_alert.py check")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_screen_alert_check.py tests/test_screen_alert_select.py -q`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add screen_alert.py tests/test_screen_alert_check.py
git commit -m "feat(alerts): push new same-day screen rows once each"
```

---

### Task 7: Wire the 5-minute cadence

**Files:**
- Create: `.github/workflows/screen_alert.yml`
- Modify: `.github/workflows/log.yml` (add a dispatch step after the existing screen tick)
- Modify: `DEPLOY.md` (new subsection after "Screen cadence")

**Interfaces:**
- Consumes: `python screen_alert.py check` (Task 6).

- [ ] **Step 1: Create `.github/workflows/screen_alert.yml`**

```yaml
name: Screen row alert

# Pushes when a bracket settling TODAY newly appears on the Screen table.
# Deliberately has NO in-repo `cron:` schedule: GitHub drops high-frequency
# schedules first (measured 2026-08-04 at 62% delivery for an hourly one), so
# one here would fire unpredictably and add load without adding reliability.
# The cadence comes from log.yml's external cron, the one reliable clock in this
# repo (measured: 100 runs, median gap 10.0 min, max 10.1), which dispatches
# `screen-alert` on every one of its own dispatch runs. Each run then checks
# TWICE five minutes apart, turning that 10-minute clock into a 5-minute alert.
on:
  repository_dispatch:
    types: [screen-alert]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: screen-alert
  cancel-in-progress: false      # a late dispatch must not kill a job mid-sleep

jobs:
  alert:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -r requirements.txt

      - name: Check now
        env:
          SCAN_GH_REPO: ${{ github.repository }}
          SCAN_GH_BRANCH: scan-data
          SCAN_GH_TOKEN: ${{ secrets.SCAN_GH_TOKEN }}
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
        run: python screen_alert.py check

      # The second half of the 5-minute cadence. Skipped on a manual run, where
      # sleeping five minutes to repeat the same check helps nobody.
      - name: Check again five minutes later
        if: github.event_name == 'repository_dispatch'
        env:
          SCAN_GH_REPO: ${{ github.repository }}
          SCAN_GH_BRANCH: scan-data
          SCAN_GH_TOKEN: ${{ secrets.SCAN_GH_TOKEN }}
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
        run: sleep 300 && python screen_alert.py check
```

- [ ] **Step 2: Add the dispatch step to `log.yml`**

Directly after the existing "Tick the mispriced-bracket screen" step, add:

```yaml
      # Same clock, no 30-minute gate: the alert wants every heartbeat it can
      # get, and its own job splits each into two checks five minutes apart.
      - name: Tick the screen row alert
        if: github.event_name == 'repository_dispatch'
        continue-on-error: true
        env:
          SCAN_GH_TOKEN: ${{ secrets.SCAN_GH_TOKEN }}
        run: |
          if [ -z "$SCAN_GH_TOKEN" ]; then
            echo "SCAN_GH_TOKEN unset — skipping alert tick"; exit 0
          fi
          code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer $SCAN_GH_TOKEN" \
            "https://api.github.com/repos/${{ github.repository }}/dispatches" \
            -d '{"event_type":"screen-alert"}') || code="000"
          if [ "$code" = "204" ]; then
            echo "screen-alert dispatched"
          else
            echo "alert tick POST failed (HTTP $code) — ignored"
          fi
```

Match the surrounding step's `if:` condition exactly — copy it from the screen-tick step above rather than assuming, so both run on the same trigger.

- [ ] **Step 3: Validate both workflow files parse**

Run:

```bash
python3 -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ['.github/workflows/screen_alert.yml','.github/workflows/log.yml']]; print('workflows parse')"
```

Expected: `workflows parse`.

- [ ] **Step 4: Document it in `DEPLOY.md`**

Add after the "Screen cadence (external cron)" section:

```markdown
### 6. Screen row alert

A new same-day row on the Screen table pushes to ntfy within ~5 minutes.

**Nothing to set up beyond the secrets you already have** (`SCAN_GH_TOKEN` for
the scan-data branch, `NTFY_TOPIC` for the push). `log.yml` dispatches a
`screen-alert` `repository_dispatch` on every one of its 10-minute runs, and
`screen_alert.yml` checks twice per run, five minutes apart.

The alert re-uses `screen_reference.json`, published by every 30-minute screen
pass, rather than recomputing the NWS forecast — one check costs ~40 Kalshi and
~20 NWS requests. If the screen stalls for more than 90 minutes the forecast
half goes quiet and only `dead` rows alert; that appears in the job log as
`reference age …min — dead rows only`.

A ticker alerts once per climate day. State lives in `screen_alert_state.json`
on `scan-data` and is written only when something fires, so quiet checks cost
no commits.

To silence it entirely, disable the **Screen row alert** workflow in the Actions
tab — the dispatch step is `continue-on-error`, so nothing else is affected.
```

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/screen_alert.yml .github/workflows/log.yml DEPLOY.md
git commit -m "feat(alerts): dispatch the screen row alert every five minutes"
```

---

### Task 8: Live check before it can buzz the phone

Both the screen and the price scanner shipped with defects that only a live pass caught. This runs the real thing once with the push stubbed.

**Files:**
- Create: `scripts/check_screen_alert.py`

**Interfaces:**
- Consumes: `screen_alert.{check, Deps, _real_deps, reference_age_minutes, alert_title, alert_body}`.

- [ ] **Step 1: Write the script**

```python
"""Run ONE screen-alert check against live data with the push stubbed. By hand.

Prints the reference's age, what the check found, and the exact notification it
would have sent. State is read but never written, so running this cannot
suppress a real alert later.

Needs SCAN_GH_REPO/SCAN_GH_BRANCH/SCAN_GH_TOKEN in the environment to read the
scan-data branch. NTFY_TOPIC is deliberately NOT used — nothing is sent.

Usage: SCAN_GH_REPO=owner/repo SCAN_GH_TOKEN=… python3 scripts/check_screen_alert.py
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

import screen_alert          # noqa: E402


def main():
    now = datetime.now(timezone.utc)
    real = screen_alert._real_deps()
    reference = real.read_reference()
    age = screen_alert.reference_age_minutes(reference, now)
    cities = len((reference or {}).get("cities") or {})
    print(f"reference: {cities} cities, age "
          f"{'unknown' if age is None else round(age, 1)} min, "
          f"forecast screen {'ON' if screen_alert.forecast_is_usable(reference, now) else 'OFF'}")
    if not cities:
        print("no reference on the data branch yet — run screen.py first")
        return 1

    sent = []
    deps = screen_alert.Deps(
        read_reference=real.read_reference,
        read_state=real.read_state,
        write_state=lambda obj: print(f"[stubbed] would write {len(obj)} day(s) of state"),
        list_markets=real.list_markets,
        fetch_obs=real.fetch_obs,
        notify=lambda title, body: sent.append((title, body)) or True,
    )
    print(f"result: {screen_alert.check(now, deps)}")
    for title, body in sent:
        print(f"\n--- would push ---\n{title}\n{body}")
    if not sent:
        print("\nnothing new this check (expected most of the time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it against live data**

Run:

```bash
SCAN_GH_REPO=jaredmcelreath-a11y/Weather-Model SCAN_GH_BRANCH=scan-data \
  SCAN_GH_TOKEN=<the PAT> python3 scripts/check_screen_alert.py
```

Expected: a reference line naming ~20 cities with an age under 90 min, then either a plausible notification or "nothing new this check". If the reference does not exist yet, trigger `scan.yml` (Actions → Kalshi multi-city price scan → Run workflow → `screen`) and re-run.

**Read the output before continuing.** Every line must name a real city, a bracket label matching Kalshi's own wording, a NO price between 20% and 90%, and a reference number that is plausible for that city today. Anything that fails that read is a bug to fix now, not after it starts pushing.

- [ ] **Step 3: Commit**

```bash
git add scripts/check_screen_alert.py
git commit -m "chore(alerts): add a live dry-run of the screen row alert"
```

---

## Done when

- `python3 -m pytest -q` passes with no failures.
- `python3 scripts/check_screen_alert.py` prints a fresh reference and a plausible (or empty) notification.
- The four retired alerts are gone from the code, their tests deleted, and `grep -rn "RESOLVED_ALERT_PCT\|_maybe_alert\|storm_body\|front_body" --include="*.py" .` returns nothing.
- Morning Recap still fires, still prefixed `Austin: ` for KAUS.
- `screen.py` writes `screen_reference.json` on every pass and its candidate log is unchanged.
- `log.yml` dispatches `screen-alert`, and `screen_alert.yml` runs two checks five minutes apart.
