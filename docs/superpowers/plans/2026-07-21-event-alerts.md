# Event Alerts (Storm / Front / Morning Recap) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three once-per-day ntfy alerts fired from the scheduled run — Storm Watch Active, Front Risk, and a 6:30 AM Morning Recap digest — with title-case titles.

**Architecture:** A new `alerts.py` module holds shared alert state I/O, pure message-builders, and a `maybe_fire_events(snap, now)` orchestrator that `scheduled_log._log_snapshots` calls after the resolved alert. State lives in `event_alert_state.json` on the `data` branch. Reuses `notify.send_ntfy` + the `NTFY_TOPIC` secret.

**Tech Stack:** Python 3.11, pytest, GitHub Actions, ntfy.

## Global Constraints

- Titles exactly: `Storm Watch Active`, `Front Risk`, `Morning Recap`.
- Storm fires on `level == "active"` **only** (not `"watch"`).
- Morning Recap fires on the first run at/after **06:30 America/Chicago**, keyed to the **local** date; Storm/Front keyed to the **climate day** (`settlement.climate_day_of(now)`).
- Each alert once per day; the three are independent (one firing never gates another).
- Best-effort everywhere: any missing field / send failure / corrupt state file skips that alert (logged) and never blocks the others or the surrounding logging.
- `alerts.py` must stay **cron-safe** — no Streamlit import at module top.
- Empty/corrupt state file (0-byte restore artifact) → treated as `{}`.

---

### Task 1: `alerts.py` — state I/O + pure message-builders

**Files:**
- Create: `alerts.py`
- Test: `tests/test_alerts_builders.py`

**Interfaces:**
- Produces:
  - `load_state(path: str) -> dict` (missing/empty/corrupt → `{}`)
  - `save_state(path: str, state: dict) -> None`
  - `storm_body(storm: dict) -> str`
  - `front_body(low: dict) -> str`
  - `recap_body(setup: dict | None, yesterday: dict | None) -> str` (`""` if `setup` falsy)

- [ ] **Step 1: Write the failing test**

Create `tests/test_alerts_builders.py`:

```python
"""alerts.py — state load/save + pure message builders."""
import alerts


def test_load_state_missing_empty_corrupt(tmp_path):
    p = tmp_path / "s.json"
    assert alerts.load_state(str(p)) == {}          # missing
    p.write_text("")
    assert alerts.load_state(str(p)) == {}          # empty
    p.write_text("{not json")
    assert alerts.load_state(str(p)) == {}          # corrupt
    p.write_text('{"storm": "2026-07-21"}')
    assert alerts.load_state(str(p)) == {"storm": "2026-07-21"}


def test_save_then_load_roundtrip(tmp_path):
    p = str(tmp_path / "s.json")
    alerts.save_state(p, {"recap": "2026-07-21"})
    assert alerts.load_state(p) == {"recap": "2026-07-21"}


def test_storm_body_with_upstream_warning():
    storm = {"level": "active", "sigma": 3.0,
             "upstream": {"active": True, "county": "Tarrant", "direction": "NW"}}
    body = alerts.storm_body(storm)
    assert "Tarrant Co (NW)" in body
    assert "±3°F" in body


def test_storm_body_without_upstream():
    storm = {"level": "active", "sigma": 2.0,
             "upstream": {"active": False, "county": None, "direction": None}}
    body = alerts.storm_body(storm)
    assert "approach" in body.lower()
    assert "±2°F" in body


def test_front_body_uses_projection_then_consensus():
    low = {"consensus": 80.0, "front_guard": {"projection": 77.0}}
    assert "≈77°F" in alerts.front_body(low)
    assert "≈80°F" in alerts.front_body({"consensus": 80.0})  # no front_guard


def test_recap_body_yesterday_and_today():
    setup = {"high": {"consensus": 101.0, "locked": False},
             "low": {"observed": 80.0, "consensus": 80.0, "locked": True}}
    yesterday = {"high": {"settled": 100, "model": 99, "exact": False},
                 "low": {"settled": 80, "model": 80, "exact": True}}
    body = alerts.recap_body(setup, yesterday)
    assert "Yesterday:" in body
    assert "High 100 (model 99, Miss +1)" in body
    assert "Low 80 (model 80, Exact" in body
    assert "Today: Low ~80 (Locked), High ~101" in body


def test_recap_body_today_only_when_no_yesterday():
    setup = {"high": {"consensus": 101.0, "locked": False},
             "low": {"observed": None, "consensus": 79.0, "locked": False}}
    body = alerts.recap_body(setup, None)
    assert "Yesterday" not in body
    assert "Today: Low ~79 (Developing), High ~101" in body


def test_recap_body_empty_without_setup():
    assert alerts.recap_body(None, None) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_alerts_builders.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'alerts'`.

- [ ] **Step 3: Write minimal implementation**

Create `alerts.py`:

```python
"""ntfy event alerts fired from the scheduled run: Storm Watch, Front Risk, and
the Morning Recap digest.

Pure message-builders + state I/O live here (unit-testable, no network/Streamlit);
`maybe_fire_events` orchestrates the once-per-day sends. Kept cron-safe — no
Streamlit import at module top.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import notify
import settlement
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)

EVENT_STATE_PATH = os.path.join(os.path.dirname(__file__), "event_alert_state.json")
RECAP_HOUR, RECAP_MINUTE = 6, 30


def load_state(path: str) -> dict:
    """Load a JSON alert-state dict, tolerating a missing/empty/corrupt file."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path: str, state: dict) -> None:
    with open(path, "w") as fh:
        json.dump(state, fh)


def storm_body(storm: dict) -> str:
    """Body for the Storm Watch Active alert."""
    sigma = storm.get("sigma") or 0.0
    up = storm.get("upstream") or {}
    if up.get("active"):
        return (f"SVR warning {up.get('county')} Co ({up.get('direction')}) · "
                f"low downside ±{sigma:g}°F")
    return f"Convective storms on the approach · low downside ±{sigma:g}°F"


def front_body(low: dict) -> str:
    """Body for the Front Risk alert."""
    fg = low.get("front_guard") or {}
    proj = fg.get("projection")
    if proj is None:
        proj = low.get("consensus")
    return f"Front may undercut tonight's low · projection ≈{proj:g}°F"


def recap_body(setup: dict | None, yesterday: dict | None) -> str:
    """Compact Morning Recap body: yesterday's scorecard (if settled) + today's
    setup. Empty string when `setup` is unavailable."""
    if not setup:
        return ""
    lines = []
    if yesterday:
        parts = []
        for var in ("high", "low"):
            g = yesterday.get(var)
            if not g:
                continue
            mark = ("Exact ✓" if g.get("exact")
                    else f"Miss {g['settled'] - g['model']:+g}")
            parts.append(f"{var.capitalize()} {g['settled']:g} "
                         f"(model {g['model']:g}, {mark})")
        if parts:
            lines.append("Yesterday: " + "; ".join(parts))
    lo = setup.get("low") or {}
    hi = setup.get("high") or {}
    lo_v = lo.get("observed")
    if lo_v is None:
        lo_v = lo.get("consensus")
    status = "Locked" if lo.get("locked") else "Developing"
    today = f"Today: Low ~{lo_v:g} ({status})" if lo_v is not None else "Today:"
    hi_v = hi.get("consensus")
    if hi_v is not None:
        today += f", High ~{hi_v:g}"
    lines.append(today)
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_alerts_builders.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add alerts.py tests/test_alerts_builders.py
git commit -m "feat: alerts.py — state I/O + storm/front/recap message builders"
```

---

### Task 2: `maybe_fire_events` orchestrator

**Files:**
- Modify: `alerts.py` (add `_build_recap_body` + `maybe_fire_events`)
- Test: `tests/test_alerts_events.py`

**Interfaces:**
- Consumes: `notify.send_ntfy`, `settlement.climate_day_of`, `load_state`/`save_state`, the builders, and (for the recap) `recap.today_setup` / `recap.yesterday_scorecard` via `_build_recap_body`.
- Produces: `alerts.maybe_fire_events(snap: dict, now: datetime) -> None`;
  `alerts._build_recap_body(snap: dict) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_alerts_events.py`:

```python
"""alerts.maybe_fire_events — triggers, once-per-day gating, independence."""
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


# 3 PM local — past the recap window, so recap fires too unless gated out.
_PM = datetime(2026, 7, 21, 15, 0, tzinfo=_TZ)


def test_storm_fires_on_active_not_watch(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends, recap="")  # suppress recap
    alerts.maybe_fire_events(_snap(level="watch"), _PM)
    assert not any(t == "Storm Watch Active" for t, _ in sends)
    alerts.maybe_fire_events(_snap(level="active"), _PM)
    assert any(t == "Storm Watch Active" for t, _ in sends)


def test_front_fires_only_when_widened(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends, recap="")
    alerts.maybe_fire_events(_snap(front=False), _PM)
    assert not any(t == "Front Risk" for t, _ in sends)
    alerts.maybe_fire_events(_snap(front=True), _PM)
    assert [t for t, _ in sends] == ["Front Risk"]


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


def test_all_three_independent_same_run(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends)
    alerts.maybe_fire_events(_snap(level="active", front=True), _PM)
    titles = sorted(t for t, _ in sends)
    assert titles == ["Front Risk", "Morning Recap", "Storm Watch Active"]


def test_empty_state_file_does_not_block(monkeypatch, tmp_path):
    sends = []
    _patch(monkeypatch, tmp_path, sends, recap="")
    (tmp_path / "ev.json").write_text("")
    alerts.maybe_fire_events(_snap(level="active"), _PM)
    assert [t for t, _ in sends] == ["Storm Watch Active"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_alerts_events.py -q`
Expected: FAIL — `AttributeError: module 'alerts' has no attribute 'maybe_fire_events'` (or `_build_recap_body` on the monkeypatch).

- [ ] **Step 3: Add `_build_recap_body` and `maybe_fire_events` to `alerts.py`**

Append to `alerts.py`:

```python
def _build_recap_body(snap: dict) -> str:
    """Assemble the Morning Recap body from yesterday's scorecard + today's setup,
    mirroring app.load_recap. Best-effort — returns "" on any failure."""
    try:
        from datetime import date
        import forecast_log
        import recap
        import settlements
        bet_rows = None
        try:
            import bet_history
            bet_rows = bet_history.fetch_rows(bet_history.BETS_START)
        except Exception:
            bet_rows = None
        yesterday = recap.yesterday_scorecard(
            date.today(), settlements.as_map("cli"),
            forecast_log.load(), bet_rows=bet_rows)
        return recap_body(recap.today_setup(snap), yesterday)
    except Exception:
        return ""


def maybe_fire_events(snap: dict, now: datetime) -> None:
    """Fire the storm/front/recap alerts, each once per day. Best-effort per
    alert (one failing never blocks another) and overall."""
    state = load_state(EVENT_STATE_PATH)
    dirty = False
    try:
        cday = settlement.climate_day_of(now).isoformat()
    except Exception:
        cday = None

    def _send(key, day, title, body):
        nonlocal dirty
        if not day or not body or state.get(key) == day:
            return
        if notify.send_ntfy(title, body):
            state[key] = day
            dirty = True
            print(f"Event alert sent: {key}")
        else:
            print(f"Event alert: send_ntfy False for {key}")

    try:
        storm = snap.get("storm") or {}
        if storm.get("level") == "active":
            _send("storm", cday, "Storm Watch Active", storm_body(storm))
    except Exception as e:
        print(f"Event alert skipped (storm): {e}")

    try:
        low = (snap.get("today") or {}).get("low") or {}
        if low.get("front_widened"):
            _send("front", cday, "Front Risk", front_body(low))
    except Exception as e:
        print(f"Event alert skipped (front): {e}")

    try:
        local = now.astimezone(_TZ)
        if (local.hour, local.minute) >= (RECAP_HOUR, RECAP_MINUTE):
            _send("recap", local.date().isoformat(), "Morning Recap",
                  _build_recap_body(snap))
    except Exception as e:
        print(f"Event alert skipped (recap): {e}")

    if dirty:
        try:
            save_state(EVENT_STATE_PATH, state)
        except Exception as e:
            print(f"Event alert state save failed: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_alerts_events.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add alerts.py tests/test_alerts_events.py
git commit -m "feat: maybe_fire_events — storm/front/recap once-per-day orchestrator"
```

---

### Task 3: Wire `alerts` into `scheduled_log.py`

**Files:**
- Modify: `scheduled_log.py` (use `alerts.load_state`; drop the local helper; call `maybe_fire_events`)
- (Regression only — no new test; `tests/test_cli_alert.py` + `tests/test_resolved_alert.py` cover the shared load.)

**Interfaces:**
- Consumes: `alerts.load_state`, `alerts.maybe_fire_events`.

- [ ] **Step 1: Import `alerts` and replace the local state loader**

In `scheduled_log.py`, add `import alerts` with the other top-level imports. Delete the local `_load_alert_state` definition (the whole function). Change both call sites:

```python
        state = _load_alert_state(STATE_PATH)
```
→
```python
        state = alerts.load_state(STATE_PATH)
```
and
```python
        state = _load_alert_state(RESOLVED_STATE_PATH)
```
→
```python
        state = alerts.load_state(RESOLVED_STATE_PATH)
```

- [ ] **Step 2: Call `maybe_fire_events` from `_log_snapshots`**

Right after the resolved-alert call:

```python
    _maybe_alert_resolved(cli_snap, now)
    alerts.maybe_fire_events(cli_snap, now)
```

- [ ] **Step 3: Run the alert-related tests for regressions**

Run: `python3 -m pytest tests/test_cli_alert.py tests/test_resolved_alert.py tests/test_alerts_builders.py tests/test_alerts_events.py -q`
Expected: PASS (all).

- [ ] **Step 4: Commit**

```bash
git add scheduled_log.py
git commit -m "feat: fire storm/front/recap event alerts from the scheduled run"
```

---

### Task 4: Persist `event_alert_state.json` in the workflow

**Files:**
- Modify: `.github/workflows/log.yml`

No unit test (config change); verified by reading the diff.

- [ ] **Step 1: Restore the state file**

In the "Restore existing logs from the data branch" step, add next to the other alert-state restores:

```yaml
            git show origin/data:event_alert_state.json > event_alert_state.json 2>/dev/null || true
```

- [ ] **Step 2: Publish the state file**

In the "Publish the logs to the data branch" step, add a copy line next to the other alert-state cp lines:

```bash
          [ -f event_alert_state.json ] && cp event_alert_state.json "$tmp/event_alert_state.json"
```

and an add line next to the other alert-state add lines:

```bash
          [ -f event_alert_state.json ] && git add -f event_alert_state.json
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/log.yml
git commit -m "ci: persist event_alert_state.json on the data branch"
```

---

### Final verification

- [ ] Run the whole suite:

Run: `python3 -m pytest -q`
Expected: all pass (existing 633 + the new builder/event tests).

- [ ] Optional live smoke test (after merge): trigger the log workflow and read the log —
  `gh workflow run log.yml --repo jaredmcelreath-a11y/Weather-Model`, then
  `gh run view <id> --log | grep -i "Event alert"`. Storm/Front only fire on a real
  risk day; Morning Recap fires only on the first run past 6:30 AM local, so a
  mid-day trigger will typically show none of them firing (state already set or
  conditions clear) — a clean run with no "skipped" errors is the pass here.
