# CLI Report Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When NWS issues the afternoon CLIDFW daily climate report (~4:41 PM CDT), show a minimal confirmation box on the Forecast page and send a one-time ntfy push with the day's locked high and low.

**Architecture:** A new `sources/nws_cli.py` fetches the latest CLI product from the NWS API and parses today's high/low out of the product text. A tiny `notify.py` POSTs to ntfy. The Forecast page (`market_view.render_page`) renders a box when today's report is in; the every-10-min `scheduled_log.py` Action sends exactly one push per day, gated by a `cli_alert_state.json` file persisted on the `data` branch.

**Tech Stack:** Python 3.11, `requests` (via `sources/common`), Streamlit, pytest, GitHub Actions.

## Global Constraints

- Location code for the CLI product is **`DFW`**, not `FWD`.
- Everything is **best-effort**: any network/parse/config failure must hide the box / skip the push and never crash the page or the scheduled run.
- ntfy push **title** is exactly `Dallas Climate Report`.
- Box + push content is **minimal**: high, low, issuance time. No bracket resolution, no market/edge callout.
- "Today's report is in" ≡ the parsed report date equals today's **climate day** (`settlement.climate_day_of(now)`). The `settlement.climate_day_of` comparison lives in the **callers** (`app.py`, `scheduled_log.py`) — `sources/nws_cli.py` must not import `settlement`.
- `cryptography` must stay ≤38.x (unrelated to this feature, but do not upgrade deps).

---

### Task 1: `sources/nws_cli.py` — fetch + parse the CLI product

**Files:**
- Create: `sources/nws_cli.py`
- Test: `tests/test_nws_cli.py`

**Interfaces:**
- Consumes: `sources.common.get_json` (`get_json(url, params=None, ttl=..., timeout=..., retries=...) -> dict`), `sources.common.TZ` (station tzinfo), `config.CACHE_TTL_SECONDS`.
- Produces:
  - `parse_cli(text: str, issued: datetime) -> dict | None` — pure parser.
  - `fetch_latest_cli(ttl: int | None = None) -> dict | None` — network fetch.
  - Both return `{report_date: date, high_f: int, low_f: int, high_time: str, low_time: str, issued: datetime}` or `None`. `issued` is tz-aware, converted to `common.TZ`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_nws_cli.py`:

```python
"""NWS CLIDFW daily climate report — parsing."""
from datetime import date, datetime, timezone

from sources import nws_cli

# Real CLIDFW product text captured 2026-07-20 (afternoon preliminary).
FIXTURE = """
000
CDUS44 KFWD 202141
CLIDFW

CLIMATE REPORT
NATIONAL WEATHER SERVICE FORT WORTH TX
441 PM CDT MON JUL 20 2026

...................................

...THE DALLAS/FORT WORTH CLIMATE SUMMARY FOR JULY 20 2026...
VALID AS OF 0400 PM LOCAL TIME.

CLIMATE NORMAL PERIOD 1991 TO 2020
CLIMATE RECORD PERIOD 1898 TO 2026


WEATHER ITEM   OBSERVED TIME   RECORD YEAR NORMAL DEPARTURE LAST
                VALUE   (LST)  VALUE       VALUE  FROM      YEAR
...................................................................
TEMPERATURE (F)
 TODAY
  MAXIMUM        100    254 PM 109    2022  96      4       95
  MINIMUM         80    615 AM  65    1920  76      4       80
  AVERAGE         90

PRECIPITATION (IN)
  TODAY            0.00          1.10 1920   0.05  -0.05     0.00
  MONTH TO DATE    1.36                      1.56  -0.20     1.52
"""

# A prior-day early-AM issuance reports the *previous* completed day.
FIXTURE_PRIOR_DAY = FIXTURE.replace("JULY 20 2026", "JULY 19 2026")

_ISSUED = datetime(2026, 7, 20, 21, 41, tzinfo=timezone.utc)


def test_parse_extracts_high_low_times_and_date():
    r = nws_cli.parse_cli(FIXTURE, _ISSUED)
    assert r["high_f"] == 100
    assert r["low_f"] == 80
    assert r["high_time"] == "254 PM"
    assert r["low_time"] == "615 AM"
    assert r["report_date"] == date(2026, 7, 20)


def test_parse_issued_is_localized():
    r = nws_cli.parse_cli(FIXTURE, _ISSUED)
    # 21:41 UTC == 16:41 local (America/Chicago, CDT)
    assert r["issued"].hour == 16
    assert r["issued"].tzinfo is not None


def test_parse_prior_day_report_carries_prior_date():
    r = nws_cli.parse_cli(FIXTURE_PRIOR_DAY, _ISSUED)
    assert r["report_date"] == date(2026, 7, 19)


def test_parse_malformed_returns_none():
    assert nws_cli.parse_cli("garbage with no fields", _ISSUED) is None
    assert nws_cli.parse_cli("", _ISSUED) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nws_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sources.nws_cli'`.

- [ ] **Step 3: Write minimal implementation**

Create `sources/nws_cli.py`:

```python
"""NWS CLIDFW daily climate report — the official settlement basis product.

NWS Fort Worth issues a preliminary CLIDFW around 4:41 PM CDT reporting the
day's (by then locked) high and low; overnight/early-AM issuances report the
prior completed day. We fetch the latest product and parse today's extremes.

The "is this today's report?" decision (comparing report_date to the climate
day) lives in the callers, which already import `settlement`; this module stays
a pure fetch+parse layer with no upward dependency.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from config import CACHE_TTL_SECONDS
from sources.common import TZ, get_json

LIST_URL = "https://api.weather.gov/products/types/CLI/locations/DFW"

_DATE_RE = re.compile(r"CLIMATE SUMMARY FOR ([A-Z]+ \d{1,2} \d{4})")
_MAX_RE = re.compile(r"^\s*MAXIMUM\s+(-?\d+)\s+(\d{1,4})\s+([AP]M)", re.M)
_MIN_RE = re.compile(r"^\s*MINIMUM\s+(-?\d+)\s+(\d{1,4})\s+([AP]M)", re.M)


def parse_cli(text: str, issued: datetime) -> dict | None:
    """Parse a CLIDFW product's text into today's extremes, or None."""
    dm = _DATE_RE.search(text)
    hm = _MAX_RE.search(text)
    nm = _MIN_RE.search(text)
    if not (dm and hm and nm):
        return None
    try:
        report_date = datetime.strptime(dm.group(1).title(), "%B %d %Y").date()
    except ValueError:
        return None
    return {
        "report_date": report_date,
        "high_f": int(hm.group(1)),
        "low_f": int(nm.group(1)),
        "high_time": f"{hm.group(2)} {hm.group(3)}",
        "low_time": f"{nm.group(2)} {nm.group(3)}",
        "issued": issued.astimezone(TZ),
    }


def fetch_latest_cli(ttl: int | None = None) -> dict | None:
    """Fetch and parse the newest CLIDFW product, or None on any failure.

    `ttl` controls the cache freshness of the product list; pass 0 for an
    always-fresh read (the scheduled Action), or a short TTL for the dashboard.
    """
    t = CACHE_TTL_SECONDS if ttl is None else ttl
    try:
        listing = get_json(LIST_URL, ttl=t)
        graph = listing.get("@graph") or []
        if not graph:
            return None
        product = get_json(graph[0]["@id"], ttl=t)
        text = product.get("productText") or ""
        issued = datetime.fromisoformat(product["issuanceTime"])
        return parse_cli(text, issued)
    except Exception:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nws_cli.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add sources/nws_cli.py tests/test_nws_cli.py
git commit -m "feat: fetch + parse NWS CLIDFW daily climate report"
```

---

### Task 2: `notify.py` — ntfy push helper

**Files:**
- Create: `notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Produces: `send_ntfy(title: str, message: str) -> bool` — POSTs to `https://ntfy.sh/<NTFY_TOPIC>`; returns success. No-op returning `False` when `NTFY_TOPIC` is unset/empty.

- [ ] **Step 1: Write the failing test**

Create `tests/test_notify.py`:

```python
"""ntfy push helper."""
import notify


def test_send_ntfy_noop_without_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert notify.send_ntfy("t", "m") is False


def test_send_ntfy_posts_with_title_and_body(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "my-secret-topic")
    calls = {}

    class _Resp:
        def raise_for_status(self):
            pass

    def fake_post(url, data=None, headers=None, timeout=None):
        calls["url"] = url
        calls["data"] = data
        calls["headers"] = headers
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    assert notify.send_ntfy("Dallas Climate Report", "High 100") is True
    assert calls["url"] == "https://ntfy.sh/my-secret-topic"
    assert calls["headers"]["Title"] == "Dallas Climate Report"
    assert b"High 100" == calls["data"]


def test_send_ntfy_swallows_errors(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.requests, "post", boom)
    assert notify.send_ntfy("t", "m") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_notify.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'notify'`.

- [ ] **Step 3: Write minimal implementation**

Create `notify.py`:

```python
"""Push notifications via ntfy (https://ntfy.sh).

The topic comes from the NTFY_TOPIC env var (a bare topic name or a full URL);
subscribe a phone to that topic in the ntfy app. Best-effort: a missing topic or
any network error is a silent no-op, so local runs without the secret don't fail.
"""

from __future__ import annotations

import os

import requests


def send_ntfy(title: str, message: str) -> bool:
    """POST `message` to the configured ntfy topic. Returns success."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    url = topic if topic.startswith("http") else f"https://ntfy.sh/{topic}"
    try:
        resp = requests.post(url, data=message.encode("utf-8"),
                             headers={"Title": title}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_notify.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add notify.py tests/test_notify.py
git commit -m "feat: ntfy push helper"
```

---

### Task 3: On-page CLI box on the Forecast page

**Files:**
- Modify: `market_view.py` (add `cli_report_html`; add `cli_report=None` param to `render_page` and render the box after the title)
- Modify: `app.py` (add cached `load_cli_report()`; pass it into `render_page` on the Kalshi/cli page only)
- Test: `tests/test_cli_report_box.py`

**Interfaces:**
- Consumes: a cli dict from `nws_cli.fetch_latest_cli` (`{report_date, high_f, low_f, high_time, low_time, issued}`), the module-level `_PANEL` style string in `market_view.py`.
- Produces: `market_view.cli_report_html(cli: dict) -> str`; `render_page(..., cli_report=None)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_report_box.py`:

```python
"""The on-page CLIDFW confirmation box."""
from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
import market_view

_TZ = ZoneInfo(TIMEZONE)


def _cli():
    return {
        "report_date": datetime(2026, 7, 20).date(),
        "high_f": 100, "low_f": 80,
        "high_time": "254 PM", "low_time": "615 AM",
        "issued": datetime(2026, 7, 20, 16, 41, tzinfo=_TZ),
    }


def test_cli_box_shows_high_low_and_issued():
    html = market_view.cli_report_html(_cli())
    assert "100" in html
    assert "80" in html
    assert "4:41" in html  # localized issuance time
    assert "CLIMATE REPORT" in html.upper()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_report_box.py -q`
Expected: FAIL — `AttributeError: module 'market_view' has no attribute 'cli_report_html'`.

- [ ] **Step 3: Add `cli_report_html` to `market_view.py`**

Add immediately after `morning_recap_html` (after its `return "".join(parts)`, around line 488). Match the existing panel idiom (`_PANEL` + colored left border, like `storm_watch_html`):

```python
def cli_report_html(cli: dict) -> str:
    """The official-CLI confirmation box: today's locked high/low + issue time.
    Caller guarantees `cli` is today's report (report_date == climate day)."""
    issued = cli["issued"].strftime("%-I:%M %p")
    return (
        f'<div style="{_PANEL}border-left:4px solid #2f9e44;">'
        f'<div style="font-weight:600">✓ OFFICIAL NWS CLIMATE REPORT</div>'
        f'<div style="opacity:0.9">High {cli["high_f"]:g}°F · '
        f'Low {cli["low_f"]:g}°F</div>'
        f'<div style="opacity:0.7">Issued {issued}</div></div>')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_report_box.py -q`
Expected: PASS.

- [ ] **Step 5: Render the box in `render_page`**

In `market_view.py`, change the `render_page` signature (line ~1852) to add the kwarg. Current:

```python
def render_page(snap, calib, adapter, load_accuracy, recap_loader=None,
```

Add `cli_report=None` to the parameter list (append it after the existing keyword args, before the closing `):`). Then right after `st.title("Dallas Daily High & Low")` (line ~1861), insert:

```python
    if cli_report:
        st.markdown(cli_report_html(cli_report), unsafe_allow_html=True)
```

- [ ] **Step 6: Wire the loader in `app.py`**

Add a cached loader near `load_portfolio_value` (after line ~261):

```python
@st.cache_data(ttl=300, show_spinner=False)
def load_cli_report():
    """Today's official CLIDFW report (high/low) if NWS has issued it, else None.
    Gated to the climate day so yesterday's overnight product never shows."""
    try:
        from datetime import datetime
        import settlement
        from sources import nws_cli
        from sources.common import TZ
        now = datetime.now(TZ)
        cli = nws_cli.fetch_latest_cli(ttl=300)
        if cli and cli["report_date"] == settlement.climate_day_of(now):
            return cli
    except Exception:
        return None
    return None
```

Then in `_page` (line ~293) pass it only on the cli basis. Change the `render_page` call:

```python
    market_view.render_page(snap, calib, adapter, accuracy_loader,
                             recap_loader=load_recap,
                             history_loader=load_calibration_history,
                             bankroll=bankroll,
                             cli_report=load_cli_report() if record_basis == "cli" else None)
```

- [ ] **Step 7: Run the box test + full suite for regressions**

Run: `python -m pytest tests/test_cli_report_box.py tests/test_market_view.py -q`
Expected: PASS (no regressions in the market view tests).

- [ ] **Step 8: Commit**

```bash
git add market_view.py app.py tests/test_cli_report_box.py
git commit -m "feat: official CLI report box on the Forecast page"
```

---

### Task 4: Once-per-day ntfy push from the scheduled run

**Files:**
- Modify: `scheduled_log.py` (add `_maybe_alert_cli`, `STATE_PATH`; call it from `main`)
- Test: `tests/test_cli_alert.py`

**Interfaces:**
- Consumes: `nws_cli.fetch_latest_cli`, `notify.send_ntfy`, `settlement.climate_day_of`.
- Produces: `scheduled_log._maybe_alert_cli(now: datetime) -> None`; module constant `scheduled_log.STATE_PATH` (path to `cli_alert_state.json`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_alert.py`:

```python
"""Once-per-day CLI push gate in scheduled_log."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE
import scheduled_log

_TZ = ZoneInfo(TIMEZONE)


def _cli(day):
    return {
        "report_date": day, "high_f": 100, "low_f": 80,
        "high_time": "254 PM", "low_time": "615 AM",
        "issued": datetime(day.year, day.month, day.day, 16, 41, tzinfo=_TZ),
    }


def _patch(monkeypatch, tmp_path, cli, sends):
    monkeypatch.setattr(scheduled_log, "STATE_PATH", str(tmp_path / "state.json"))
    from sources import nws_cli
    import notify
    monkeypatch.setattr(nws_cli, "fetch_latest_cli", lambda ttl=None: cli)

    def fake_send(title, message):
        sends.append((title, message))
        return True

    monkeypatch.setattr(notify, "send_ntfy", fake_send)


def test_alerts_once_per_day(monkeypatch, tmp_path):
    day = date(2026, 7, 20)
    now = datetime(2026, 7, 20, 16, 45, tzinfo=_TZ)
    sends = []
    _patch(monkeypatch, tmp_path, _cli(day), sends)

    scheduled_log._maybe_alert_cli(now)
    scheduled_log._maybe_alert_cli(now)  # later cron run, same day
    assert len(sends) == 1
    assert sends[0][0] == "Dallas Climate Report"
    assert "100" in sends[0][1] and "80" in sends[0][1]


def test_no_alert_when_report_is_not_today(monkeypatch, tmp_path):
    now = datetime(2026, 7, 20, 6, 50, tzinfo=_TZ)
    sends = []
    _patch(monkeypatch, tmp_path, _cli(date(2026, 7, 19)), sends)  # prior-day product
    scheduled_log._maybe_alert_cli(now)
    assert sends == []


def test_next_day_alerts_again(monkeypatch, tmp_path):
    sends = []
    # Day 1
    _patch(monkeypatch, tmp_path, _cli(date(2026, 7, 20)), sends)
    scheduled_log._maybe_alert_cli(datetime(2026, 7, 20, 16, 45, tzinfo=_TZ))
    # Day 2 — same state file, new report
    _patch(monkeypatch, tmp_path, _cli(date(2026, 7, 21)), sends)
    scheduled_log._maybe_alert_cli(datetime(2026, 7, 21, 16, 45, tzinfo=_TZ))
    assert len(sends) == 2
```

Note: `_patch` reuses the same `tmp_path` across the two calls in `test_next_day_alerts_again`, so the state file persists between them (same directory) — the pytest `tmp_path` fixture is one directory per test.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_alert.py -q`
Expected: FAIL — `AttributeError: module 'scheduled_log' has no attribute '_maybe_alert_cli'`.

- [ ] **Step 3: Implement `_maybe_alert_cli` in `scheduled_log.py`**

Add imports and constants near the top (after the existing `import settlement` line ~19). Add:

```python
import json
import os
```

near the other stdlib imports, and after the module imports add:

```python
STATE_PATH = os.path.join(os.path.dirname(__file__), "cli_alert_state.json")
```

Then add the function (place it above `main`):

```python
def _maybe_alert_cli(now: datetime) -> None:
    """Send one ntfy push the first time today's CLIDFW report is seen.

    Fires from the 10-min Action so it works even when no one has the dashboard
    open. `STATE_PATH` (persisted on the data branch) records the last-alerted
    day so later runs stay quiet. Best-effort: any failure is logged and skipped.
    """
    try:
        import notify
        from sources import nws_cli
        cli = nws_cli.fetch_latest_cli(ttl=0)  # always fresh in the cron
        today = settlement.climate_day_of(now)
        if not cli or cli["report_date"] != today:
            return
        state = {}
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as fh:
                state = json.load(fh)
        if state.get("last_alerted_day") == today.isoformat():
            return
        msg = (f'High {cli["high_f"]:g}°F · Low {cli["low_f"]:g}°F'
               f' · issued {cli["issued"].strftime("%-I:%M %p")}')
        if notify.send_ntfy("Dallas Climate Report", msg):
            with open(STATE_PATH, "w") as fh:
                json.dump({"last_alerted_day": today.isoformat()}, fh)
            print(f"CLI alert sent for {today}")
    except Exception as e:
        print(f"CLI alert skipped: {e}")
```

- [ ] **Step 4: Call it from `main`**

At the very start of `main()` (line ~102, before `calib = calibration.get(...)`), so alerts fire even during a calibration outage:

```python
def main() -> None:
    from sources.common import TZ
    _maybe_alert_cli(datetime.now(TZ))
    calib = calibration.get(refresh=True)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_alert.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add scheduled_log.py tests/test_cli_alert.py
git commit -m "feat: once-per-day CLI report ntfy push from the scheduled run"
```

---

### Task 5: Persist alert state + pass the ntfy topic in the workflow

**Files:**
- Modify: `.github/workflows/log.yml`

No unit test (CI/config change); verified by reading the diff. This task makes the once-per-day state survive across runs and gives the Action the ntfy topic.

- [ ] **Step 1: Restore the state file from the data branch**

In the "Restore existing logs from the data branch" step, add a line alongside the other `git show origin/data:...` restores:

```yaml
            git show origin/data:cli_alert_state.json > cli_alert_state.json 2>/dev/null || true
```

- [ ] **Step 2: Pass the ntfy topic to the run**

Add an `env:` block to the "Append this snapshot" step so `scheduled_log.py` can read the topic:

```yaml
      - name: Append this snapshot
        env:
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
        run: python scheduled_log.py
```

- [ ] **Step 3: Publish the state file to the data branch**

In the "Publish the logs to the data branch" step, add a copy line next to the other `cp ...` lines:

```bash
          [ -f cli_alert_state.json ] && cp cli_alert_state.json "$tmp/cli_alert_state.json"
```

and an add line next to the other `git add -f ...` lines:

```bash
          [ -f cli_alert_state.json ] && git add -f cli_alert_state.json
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/log.yml
git commit -m "ci: persist CLI alert state + pass NTFY_TOPIC to the scheduled run"
```

- [ ] **Step 5: Manual setup note (report to the user, do not commit)**

The user must, outside the code:
1. Add a GitHub Actions secret `NTFY_TOPIC` = a hard-to-guess topic name (anyone who knows it can read the alerts).
2. Subscribe their phone to that topic in the ntfy app.

---

### Final verification

- [ ] Run the whole suite:

Run: `python -m pytest -q`
Expected: all pass (existing ~531 + the new tests).

- [ ] Sanity-check the box against live data (optional, uses the verify skill):

The Forecast page shows the green "OFFICIAL NWS CLIMATE REPORT" box only after ~4:41 PM CDT once today's CLIDFW is issued; before then it is absent. This is time-dependent, so a same-day afternoon check is the real confirmation.
