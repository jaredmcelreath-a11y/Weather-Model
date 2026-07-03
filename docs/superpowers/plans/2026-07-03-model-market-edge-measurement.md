# Betting-time Edge Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log the model + Kalshi market at fixed betting-time slots (15:00-17:00 CDT), join to settlement, and report whether the model beats the market and whether today's live continuous-minus-hourly gap predicts the settled gap better than the flat +0.89 — without changing any live forecast logic.

**Architecture:** A new `betting_log.py` writes a *separate*, slot-keyed JSONL (`betting_log.jsonl`) so the every-15-min scheduled run no longer clobbers same-day snapshots. `scheduled_log.main()` calls it, guarded by a slot check, reusing the CLI snapshot it already builds. A new `edge_report.py` joins that log with the existing `settlements.jsonl` and emits CSVs + an assessment into a dated benchmark folder. No `model.py` math changes.

**Tech Stack:** Python 3.11, stdlib only (`json`, `os`, `datetime`, `zoneinfo`, `csv`), `pytest`. Reuses `model.snapshot`, `model._offset_bucket`, `sources.kalshi.implied_block`, `settlements.as_map`.

## Global Constraints

- Python 3.11; stdlib + existing deps only — no new packages.
- Timezone is always `config.TIMEZONE` via `zoneinfo.ZoneInfo`; all slot logic is in local time.
- Each log module owns its own `_parse` / `_write` / `load` (repo pattern; see `settlements.py`, `forecast_log.py`). Do not import their private helpers.
- `betting_log.jsonl` is keyed on `(target_date, variable, capture_slot)` and upserts in place.
- Capture slots (local CDT clock labels): `15:00, 15:30, 16:00, 16:30, 17:00`; slot tolerance `±7 min`.
- Boundary day = model CLI consensus within `0.5°F` of an even|odd Kalshi bin edge. (NOT 1.0: with Kalshi edges 2°F apart, a 1.0 half-width flags every realistic temp as a boundary — degenerate. 0.5 = the outer half of a bin.)
- No live forecast path may read betting_log/edge_report values (no lookahead).
- Commit messages end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

### Task 1: Slot detection (`betting_log.current_slot`)

**Files:**
- Create: `betting_log.py`
- Test: `tests/test_betting_log.py`

**Interfaces:**
- Produces: `SLOTS: list[str]`, `SLOT_TOLERANCE_MIN: int`, `current_slot(now: datetime, slots=SLOTS, tol_min=SLOT_TOLERANCE_MIN) -> str | None` (returns the slot label, e.g. `"15:30"`, when `now` is within `tol_min` of a slot in local time; else `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_betting_log.py
from datetime import datetime
from zoneinfo import ZoneInfo

import betting_log
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _at(h, m):
    return datetime(2026, 7, 3, h, m, tzinfo=_TZ)


def test_current_slot_exact_match():
    assert betting_log.current_slot(_at(15, 30)) == "15:30"


def test_current_slot_within_tolerance():
    assert betting_log.current_slot(_at(15, 4)) == "15:00"    # +4 min
    assert betting_log.current_slot(_at(16, 24)) == "16:30"   # -6 min


def test_current_slot_outside_tolerance_is_none():
    assert betting_log.current_slot(_at(15, 12)) is None      # 12 min off any slot


def test_current_slot_all_five_slots_defined():
    assert betting_log.SLOTS == ["15:00", "15:30", "16:00", "16:30", "17:00"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_betting_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'betting_log'`

- [ ] **Step 3: Write minimal implementation**

```python
# betting_log.py
"""Betting-time forward log — a slot-keyed snapshot of the model + Kalshi market
at fixed afternoon clock times (15:00-17:00 CDT), so the model-vs-market edge and
the settlement-gap predictor can be measured at the moment bets are placed.

Separate from forecast_log.jsonl on purpose: forecast_log upserts on
(target_date, variable, lead_bucket) and would overwrite the same-day row every
run. This log keys on the capture slot, so each afternoon snapshot persists.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from config import TIMEZONE
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)
_PATH = os.path.join(os.path.dirname(__file__), "betting_log.jsonl")

SLOTS = ["15:00", "15:30", "16:00", "16:30", "17:00"]
SLOT_TOLERANCE_MIN = 7


def current_slot(now: datetime, slots=SLOTS, tol_min=SLOT_TOLERANCE_MIN) -> str | None:
    """Slot label if `now` is within `tol_min` minutes of a slot (local time), else None."""
    local = now.astimezone(TZ)
    for s in slots:
        hh, mm = (int(x) for x in s.split(":"))
        slot_dt = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if abs((local - slot_dt).total_seconds()) <= tol_min * 60:
            return s
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_betting_log.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add betting_log.py tests/test_betting_log.py
git commit -m "feat: betting-time slot detection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Row builder + slot-keyed upsert (`betting_log.record`)

**Files:**
- Modify: `betting_log.py`
- Test: `tests/test_betting_log.py`

**Interfaces:**
- Consumes: a CLI snapshot dict (`model.snapshot(..., settle_offset=off, continuous_obs=True)`) with `snapshot["today"] = {"day": iso, "high": {...}, "low": {...}}`, each variable dict carrying `consensus`, `probabilities` ({label: prob}), `observed_so_far`, `observed_continuous`, `peak_locked`, `sigma_used`; and optionally `snapshot["market"] = {"today": {"high": {"ev", "buckets", "volume"}, "low": {...}}}`. An hourly snapshot dict (`model.snapshot(calib)`) for the pre-offset center. A `calib` dict with `calib["settlement_offset"]`.
- Produces: `record(cli_snapshot, hourly_snapshot, slot, calib, path=None) -> None` (upserts today's high & low rows on key `(target_date, variable, capture_slot)`), `load(path=None) -> list[dict]`, `_key(rec) -> tuple`, `_row(...) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_betting_log.py
import os

_CLI = {
    "today": {
        "day": "2026-07-03",
        "high": {"consensus": 97.9, "probabilities": {"97": 0.4, "98": 0.35, "96": 0.15, "99": 0.1},
                 "observed_so_far": 91.94, "observed_continuous": 93.2,
                 "peak_locked": False, "sigma_used": 1.1},
        "low": {"consensus": 78.0, "probabilities": {"78": 0.5, "77": 0.3, "79": 0.2},
                "observed_so_far": 79.0, "observed_continuous": 79.0,
                "peak_locked": True, "sigma_used": 0.8},
    },
    "market": {"today": {
        "high": {"ev": 96.9, "buckets": [[None, 96, 0.3], [97, 98, 0.6], [99, 100, 0.1]], "volume": 5000.0},
        "low": {"ev": 78.1, "buckets": [[77, 78, 0.7], [79, 80, 0.3]], "volume": 500.0},
    }},
}
_HOURLY = {"today": {"day": "2026-07-03",
                     "high": {"consensus": 97.0}, "low": {"consensus": 78.0}}}
_CALIB = {"settlement_offset": {"high": 0.89, "high_std": 0.77, "low": -0.33, "low_std": 0.47}}


def test_record_writes_today_high_and_low(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)
    rows = betting_log.load(p)
    assert {r["variable"] for r in rows} == {"high", "low"}
    hi = next(r for r in rows if r["variable"] == "high")
    assert hi["capture_slot"] == "15:30"
    assert hi["target_date"] == "2026-07-03"
    assert hi["cli_consensus"] == 97.9
    assert hi["hourly_consensus"] == 97.0
    assert hi["flat_offset"] == 0.89
    assert round(hi["live_gap"], 2) == 1.26        # 93.2 - 91.94
    assert hi["peak_locked"] is False
    assert hi["market_ev"] == 96.9
    assert hi["model_bins"][0] == ["97", 0.4]      # top model bin
    assert hi["market_buckets"][1] == [97, 98, 0.6]


def test_record_upserts_same_slot(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)   # same slot again
    rows = [r for r in betting_log.load(p) if r["variable"] == "high"]
    assert len(rows) == 1                                        # overwritten, not appended


def test_record_distinct_slots_both_persist(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "15:00", _CALIB, path=p)
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)
    slots = sorted(r["capture_slot"] for r in betting_log.load(p) if r["variable"] == "high")
    assert slots == ["15:00", "15:30"]


def test_record_market_absent_is_omitted(tmp_path):
    p = str(tmp_path / "b.jsonl")
    cli_no_market = {"today": _CLI["today"]}                     # no "market" key
    betting_log.record(cli_no_market, _HOURLY, "16:00", _CALIB, path=p)
    hi = next(r for r in betting_log.load(p) if r["variable"] == "high")
    assert "market_ev" not in hi and "market_buckets" not in hi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_betting_log.py -k record -v`
Expected: FAIL — `AttributeError: module 'betting_log' has no attribute 'record'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to betting_log.py
import model


def _parse(text: str) -> list[dict]:
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def _write(rows: list[dict], path: str) -> None:
    with open(path, "w") as fh:
        for rec in rows:
            fh.write(json.dumps(rec) + "\n")


def load(path: str | None = None) -> list[dict]:
    path = path or _PATH
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return _parse(fh.read())


def _key(rec: dict) -> tuple:
    return (rec["target_date"], rec["variable"], rec["capture_slot"])


def _top_bins(probabilities: dict, n: int = 5) -> list:
    items = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
    return [[label, round(p, 4)] for label, p in items[:n]]


def _row(day: str, variable: str, slot: str, cli_var: dict, hourly_var: dict,
         market_var: dict | None, flat_offset: float, captured: str) -> dict:
    obs = cli_var.get("observed_so_far")
    cont = cli_var.get("observed_continuous")
    live_gap = (cont - obs) if (obs is not None and cont is not None) else None
    rec = {
        "target_date": day,
        "variable": variable,
        "capture_slot": slot,
        "captured_at": captured,
        "cli_consensus": cli_var.get("consensus"),
        "hourly_consensus": (hourly_var or {}).get("consensus"),
        "flat_offset": flat_offset,
        "live_gap": live_gap,
        "observed_so_far": obs,
        "observed_continuous": cont,
        "peak_locked": cli_var.get("peak_locked"),
        "sigma_used": cli_var.get("sigma_used"),
        "model_bins": _top_bins(cli_var.get("probabilities") or {}),
    }
    if market_var:
        rec["market_ev"] = market_var.get("ev")
        rec["market_buckets"] = market_var.get("buckets")
    return rec


def record(cli_snapshot: dict, hourly_snapshot: dict, slot: str, calib: dict,
           path: str | None = None) -> None:
    """Upsert today's high & low betting-time rows for `slot`."""
    today = cli_snapshot.get("today")
    if not today:
        return
    day = today["day"]
    from datetime import date as _date
    day_d = _date.fromisoformat(day)
    captured = cli_snapshot.get("updated") or datetime.now(TZ).isoformat(timespec="seconds")
    market_today = (cli_snapshot.get("market") or {}).get("today", {})
    hourly_today = (hourly_snapshot or {}).get("today", {})

    new_recs = []
    for variable in ("high", "low"):
        cli_var = today.get(variable)
        if not cli_var or not cli_var.get("probabilities"):
            continue
        flat_offset, _std = model._offset_bucket(
            calib.get("settlement_offset"), variable, day_d, calib)
        new_recs.append(_row(day, variable, slot, cli_var,
                             hourly_today.get(variable), market_today.get(variable),
                             flat_offset, captured))

    target = path or _PATH
    rows = load(target)
    index = {_key(r): i for i, r in enumerate(rows)}
    for rec in new_recs:
        k = _key(rec)
        if k in index:
            rows[index[k]] = rec
        else:
            index[k] = len(rows)
            rows.append(rec)
    _write(rows, target)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_betting_log.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add betting_log.py tests/test_betting_log.py
git commit -m "feat: slot-keyed betting-time row builder + upsert

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire the capture into the scheduler + standalone entry

**Files:**
- Modify: `scheduled_log.py:25-53` (add guarded betting capture)
- Modify: `betting_log.py` (add `main()`)
- Test: `tests/test_betting_log.py`, `tests/test_scheduled_betting.py`

**Interfaces:**
- Consumes: `betting_log.current_slot`, `betting_log.record`, `model.snapshot`.
- Produces: `betting_log.main() -> None` (standalone dry-run capture); a guarded block in `scheduled_log.main()` that calls `betting_log.record(cli_snap, hourly_snap, slot, calib)` only when `current_slot(now)` is not None.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduled_betting.py
"""The scheduled run captures a betting-log row only inside a slot window."""
from datetime import datetime
from zoneinfo import ZoneInfo

import betting_log
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def test_capture_if_slot_records_inside_window(tmp_path, monkeypatch):
    p = str(tmp_path / "b.jsonl")
    monkeypatch.setattr(betting_log, "_PATH", p)
    cli = {"today": {"day": "2026-07-03",
                     "high": {"consensus": 97.9, "probabilities": {"98": 1.0},
                              "observed_so_far": 92.0, "observed_continuous": 93.0,
                              "peak_locked": False, "sigma_used": 1.1}}}
    hourly = {"today": {"high": {"consensus": 97.0}}}
    calib = {"settlement_offset": {"high": 0.89}}
    now = datetime(2026, 7, 3, 15, 32, tzinfo=_TZ)                 # inside 15:30 ±7
    betting_log.capture_if_slot(cli, hourly, calib, now=now)
    rows = betting_log.load(p)
    assert len(rows) == 1 and rows[0]["capture_slot"] == "15:30"


def test_capture_if_slot_noop_outside_window(tmp_path, monkeypatch):
    p = str(tmp_path / "b.jsonl")
    monkeypatch.setattr(betting_log, "_PATH", p)
    cli = {"today": {"day": "2026-07-03",
                     "high": {"consensus": 97.9, "probabilities": {"98": 1.0},
                              "observed_so_far": 92.0, "observed_continuous": 93.0,
                              "peak_locked": False, "sigma_used": 1.1}}}
    now = datetime(2026, 7, 3, 12, 0, tzinfo=_TZ)                  # no slot
    betting_log.capture_if_slot(cli, {"today": {}}, {}, now=now)
    assert betting_log.load(p) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scheduled_betting.py -v`
Expected: FAIL — `AttributeError: module 'betting_log' has no attribute 'capture_if_slot'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to betting_log.py
def capture_if_slot(cli_snapshot: dict, hourly_snapshot: dict, calib: dict,
                    now: datetime | None = None) -> str | None:
    """If `now` falls in a betting slot, record the snapshot and return the slot."""
    now = now or datetime.now(TZ)
    slot = current_slot(now)
    if slot is None:
        return None
    record(cli_snapshot, hourly_snapshot, slot, calib)
    return slot


def main() -> None:
    """Standalone capture (dry-run / manual). The scheduler uses capture_if_slot
    with the snapshot it already built."""
    import calibration
    from datetime import date
    from sources import kalshi
    calib = calibration.get(refresh=True)
    off = (calib or {}).get("settlement_offset")
    cli = model.snapshot(calib, settle_offset=off, continuous_obs=True)
    hourly = model.snapshot(calib)
    try:
        today = date.fromisoformat(cli["today"]["day"])
        tomorrow = date.fromisoformat(cli["tomorrow"]["day"])
        cli["market"] = kalshi.implied_block(today, tomorrow)
    except Exception as e:
        print(f"market block skipped: {e}")
    slot = capture_if_slot(cli, hourly, calib)
    print(f"betting capture: slot={slot}")


if __name__ == "__main__":
    main()
```

```python
# scheduled_log.py — add import at top with the others
import betting_log
```

```python
# scheduled_log.py — insert after line 42 (consensus_log.record(...)), before the
# settlements try-block. Reuse cli_snap + its attached market; build the hourly
# center only when a slot actually matches (5x/day), so no extra fetch otherwise.
    try:
        from datetime import datetime as _dt
        from betting_log import TZ as _BTZ
        if betting_log.current_slot(_dt.now(_BTZ)) is not None:
            hourly_snap = model.snapshot(calib)
            slot = betting_log.capture_if_slot(cli_snap, hourly_snap, calib)
            print(f"betting-time capture at slot {slot}")
    except Exception as e:
        print(f"betting capture skipped: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scheduled_betting.py tests/test_betting_log.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add betting_log.py scheduled_log.py tests/test_scheduled_betting.py
git commit -m "feat: guarded betting-time capture in the scheduled run

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Persist betting_log.jsonl on the data branch (workflow)

**Files:**
- Modify: `.github/workflows/log.yml` (restore + publish steps)

**Interfaces:** none (CI config). Verified by reading the workflow and a live run.

- [ ] **Step 1: Read the current restore/publish steps**

Run: `grep -n "forecast_log.jsonl\|Restore\|Publish\|git add\|origin data" .github/workflows/log.yml`
Expected: shows the "Restore existing logs from the data branch" and "Publish the logs to the data branch" steps that currently move `forecast_log.jsonl` (and `consensus_history.jsonl`, `settlements.jsonl`).

- [ ] **Step 2: Add betting_log.jsonl alongside the existing logs**

In the **Restore** step, wherever `forecast_log.jsonl` is checked out from `origin/data` (e.g. `git show origin/data:forecast_log.jsonl > forecast_log.jsonl || true`), add the same line for the new file:

```bash
git show origin/data:betting_log.jsonl > betting_log.jsonl 2>/dev/null || true
```

In the **Publish** step, wherever `forecast_log.jsonl` is staged (e.g. `git add -f forecast_log.jsonl consensus_history.jsonl settlements.jsonl`), add `betting_log.jsonl`:

```bash
git add -f betting_log.jsonl || true
```

(Match the exact surrounding syntax/quoting the step already uses; the two edits mirror how `forecast_log.jsonl` is handled.)

- [ ] **Step 3: Verify the YAML is well-formed**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/log.yml')); print('ok')"`
Expected: `ok` (if PyYAML is unavailable locally, visually confirm indentation matches the sibling lines instead)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/log.yml
git commit -m "ci: carry betting_log.jsonl on the data branch

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Report bin + boundary helpers (`edge_report`)

**Files:**
- Create: `edge_report.py`
- Test: `tests/test_edge_report.py`

**Interfaces:**
- Produces: `settled_bucket(temp: float, buckets: list) -> tuple | None` (the `[lo, hi, prob]`→`(lo, hi)` bucket containing `temp`, `lo`/`hi` may be `None` for open ends), `top_bucket(buckets: list) -> tuple | None` (highest-prob bucket as `(lo, hi)`), `is_boundary(consensus: float, half_width: float = 0.5) -> bool` (True when `consensus` is within `half_width` of an even|odd Kalshi edge, i.e. an even+0.5 value like 96.5).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edge_report.py
import edge_report

_BUCKETS = [[None, 96, 0.3], [97, 98, 0.6], [99, 100, 0.1]]


def test_settled_bucket_closed_range():
    assert edge_report.settled_bucket(97.0, _BUCKETS) == (97, 98)
    assert edge_report.settled_bucket(98.0, _BUCKETS) == (97, 98)


def test_settled_bucket_open_low_end():
    assert edge_report.settled_bucket(95.0, _BUCKETS) == (None, 96)


def test_settled_bucket_miss_returns_none():
    assert edge_report.settled_bucket(105.0, _BUCKETS) is None


def test_top_bucket():
    assert edge_report.top_bucket(_BUCKETS) == (97, 98)


def test_is_boundary():
    # Kalshi even|odd edges sit at even+0.5 (...94.5, 96.5, 98.5...).
    assert edge_report.is_boundary(96.5) is True          # on the 96|97 edge, dist 0
    assert edge_report.is_boundary(97.0) is True          # 0.5 from 96.5
    assert edge_report.is_boundary(95.4) is False         # 1.1 from 96.5
    assert edge_report.is_boundary(97.6) is False         # 1.1 from 96.5 and 98.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_edge_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edge_report'`

- [ ] **Step 3: Write minimal implementation**

```python
# edge_report.py
"""Join betting_log with settlements and report model-vs-market edge and the
flat-vs-live settlement-offset predictor. Analysis only — no live path reads this.
"""
from __future__ import annotations

import math


def settled_bucket(temp: float, buckets: list) -> tuple | None:
    """The (lo, hi) Kalshi bucket that `temp` falls in; open ends use None."""
    for lo, hi, _p in buckets:
        lo_ok = lo is None or temp >= lo
        hi_ok = hi is None or temp <= hi
        if lo_ok and hi_ok:
            return (lo, hi)
    return None


def top_bucket(buckets: list) -> tuple | None:
    if not buckets:
        return None
    lo, hi, _p = max(buckets, key=lambda b: b[2])
    return (lo, hi)


def is_boundary(consensus: float, half_width: float = 0.5) -> bool:
    """True when consensus is within half_width of an even|odd Kalshi edge (even+0.5)."""
    edges = [e + 0.5 for e in range(60, 120, 2)]   # ...94.5, 96.5, 98.5...
    return min(abs(consensus - e) for e in edges) <= half_width
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_edge_report.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add edge_report.py tests/test_edge_report.py
git commit -m "feat: edge-report bin + boundary helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Join betting_log × settlements

**Files:**
- Modify: `edge_report.py`
- Test: `tests/test_edge_report.py`

**Interfaces:**
- Consumes: `betting_log.load`, `settlements.as_map(basis)`.
- Produces: `join(betting_rows: list[dict], cli_map: dict, hourly_map: dict) -> list[dict]` — each input row for a settled day, augmented with `settled_cli` (float), `settled_hourly` (float), `actual_gap` (`settled_cli - settled_hourly`). Rows whose `target_date` has no settlement are dropped.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_edge_report.py
from datetime import date

_ROWS = [
    {"target_date": "2026-07-01", "variable": "high", "capture_slot": "15:30",
     "cli_consensus": 97.9, "flat_offset": 0.89, "live_gap": 1.2},
    {"target_date": "2026-07-09", "variable": "high", "capture_slot": "15:30",  # unsettled
     "cli_consensus": 99.0, "flat_offset": 0.89, "live_gap": 0.5},
]
_CLI_MAP = {date(2026, 7, 1): (98.0, 79.0)}
_HOURLY_MAP = {date(2026, 7, 1): (97.0, 79.0)}


def test_join_attaches_settlement_and_gap():
    out = edge_report.join(_ROWS, _CLI_MAP, _HOURLY_MAP)
    assert len(out) == 1                                  # unsettled row dropped
    r = out[0]
    assert r["settled_cli"] == 98.0
    assert r["settled_hourly"] == 97.0
    assert r["actual_gap"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_edge_report.py -k join -v`
Expected: FAIL — `AttributeError: module 'edge_report' has no attribute 'join'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to edge_report.py
from datetime import date as _date


def join(betting_rows: list[dict], cli_map: dict, hourly_map: dict) -> list[dict]:
    """Augment each settled row with settled_cli/settled_hourly/actual_gap."""
    out = []
    for r in betting_rows:
        d = _date.fromisoformat(r["target_date"])
        if d not in cli_map or d not in hourly_map:
            continue
        vi = 0 if r["variable"] == "high" else 1
        settled_cli = cli_map[d][vi]
        settled_hourly = hourly_map[d][vi]
        out.append({**r,
                    "settled_cli": settled_cli,
                    "settled_hourly": settled_hourly,
                    "actual_gap": settled_cli - settled_hourly})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_edge_report.py -k join -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add edge_report.py tests/test_edge_report.py
git commit -m "feat: join betting-log rows with settlements

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Metrics — model-vs-market (Q1) and flat-vs-live offset (Q2)

**Files:**
- Modify: `edge_report.py`
- Test: `tests/test_edge_report.py`

**Interfaces:**
- Consumes: joined rows from Task 6, helpers from Task 5.
- Produces: `metrics(joined: list[dict]) -> dict` keyed by `(capture_slot, variable)` → `{"n", "model_mae", "market_mae", "disagreements", "model_bin_wins", "market_bin_wins", "flat_rmse", "live_rmse", "flip_toward", "flip_away", "n_boundary"}`. Only high rows compute Q2 offset stats (`live_gap`/`flat_offset` vs `actual_gap`); low rows leave those `None`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_edge_report.py
def _hi(slot, cli, mkt_ev, buckets, settled_cli, settled_hourly, live_gap, flat=0.89,
        top_model=None):
    return {"capture_slot": slot, "variable": "high", "cli_consensus": cli,
            "market_ev": mkt_ev, "market_buckets": buckets,
            "model_bins": top_model or [["%d" % round(cli), 1.0]],
            "settled_cli": settled_cli, "settled_hourly": settled_hourly,
            "actual_gap": settled_cli - settled_hourly, "live_gap": live_gap,
            "flat_offset": flat}


def test_metrics_mae_and_offset():
    joined = [
        # model says 98 (right), market EV 96.9 (off by 1.1); settled 98/hourly 97, gap 1.0
        _hi("15:30", 98.0, 96.9, [[None, 96, 0.2], [97, 98, 0.8]], 98.0, 97.0, 1.2),
        # model 95.9 (off 0.1), market 96.1 (off 0.1); settled 96/hourly 95, gap 1.0
        _hi("15:30", 95.9, 96.1, [[95, 96, 0.9], [97, 98, 0.1]], 96.0, 95.0, 0.8),
    ]
    m = edge_report.metrics(joined)
    key = ("15:30", "high")
    assert m[key]["n"] == 2
    assert round(m[key]["model_mae"], 2) == 0.05       # |98-98|, |95.9-96| -> (0+0.1)/2
    assert round(m[key]["market_mae"], 2) == 0.60      # (1.1+0.1)/2
    # Q2: live_gap (1.2, 0.8) vs flat (0.89) predicting actual_gap (1.0, 1.0)
    # flat rmse = sqrt(((0.89-1)^2)*2/2)=0.11 ; live rmse = sqrt((0.2^2+0.2^2)/2)=0.2
    assert round(m[key]["flat_rmse"], 2) == 0.11
    assert round(m[key]["live_rmse"], 2) == 0.20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_edge_report.py -k metrics -v`
Expected: FAIL — `AttributeError: module 'edge_report' has no attribute 'metrics'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to edge_report.py
def _rmse(pairs):
    return math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs)) if pairs else None


def _mae(errs):
    return sum(abs(e) for e in errs) / len(errs) if errs else None


def metrics(joined: list[dict]) -> dict:
    groups: dict = {}
    for r in joined:
        groups.setdefault((r["capture_slot"], r["variable"]), []).append(r)

    out = {}
    for key, rows in groups.items():
        variable = key[1]
        model_err = [r["cli_consensus"] - r["settled_cli"] for r in rows]
        market_err = [r["market_ev"] - r["settled_cli"] for r in rows if r.get("market_ev") is not None]

        disagreements = model_bin_wins = market_bin_wins = 0
        for r in rows:
            if not r.get("market_buckets"):
                continue
            model_b = settled_bucket(r["cli_consensus"], r["market_buckets"])
            market_b = top_bucket(r["market_buckets"])
            actual_b = settled_bucket(r["settled_cli"], r["market_buckets"])
            if model_b != market_b:
                disagreements += 1
                if model_b == actual_b:
                    model_bin_wins += 1
                elif market_b == actual_b:
                    market_bin_wins += 1

        entry = {
            "n": len(rows),
            "model_mae": _mae(model_err),
            "market_mae": _mae(market_err),
            "disagreements": disagreements,
            "model_bin_wins": model_bin_wins,
            "market_bin_wins": market_bin_wins,
            "n_boundary": sum(1 for r in rows if is_boundary(r["cli_consensus"])),
            "flat_rmse": None, "live_rmse": None, "flip_toward": None, "flip_away": None,
        }
        if variable == "high":
            og = [r for r in rows if r.get("live_gap") is not None and r.get("actual_gap") is not None]
            entry["flat_rmse"] = _rmse([(r["flat_offset"], r["actual_gap"]) for r in og])
            entry["live_rmse"] = _rmse([(r["live_gap"], r["actual_gap"]) for r in og])
            toward = away = 0
            for r in og:
                flat_pred = round(r["settled_hourly"] + r["flat_offset"])
                live_pred = round(r["settled_hourly"] + r["live_gap"])
                truth = round(r["settled_cli"])
                if flat_pred != live_pred:
                    if live_pred == truth:
                        toward += 1
                    elif flat_pred == truth:
                        away += 1
            entry["flip_toward"], entry["flip_away"] = toward, away
        out[key] = entry
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_edge_report.py -k metrics -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add edge_report.py tests/test_edge_report.py
git commit -m "feat: edge metrics (model-vs-market + flat-vs-live offset)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Report writer → dated benchmark folder

**Files:**
- Modify: `edge_report.py`
- Test: `tests/test_edge_report.py`

**Interfaces:**
- Consumes: `metrics` output.
- Produces: `write_report(metrics_by_key: dict, out_dir: str) -> list[str]` — writes `metrics.csv` (one row per `(slot, variable)` with every metric column) and `ASSESSMENT.md` (headline: at the 15:00/15:30 slots, model vs market MAE, disagreement win split, flat vs live RMSE, boundary count, and the decision-gate verdict text). Returns the list of written paths. Creates `out_dir` if absent.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_edge_report.py
import os


def test_write_report_creates_files(tmp_path):
    m = {("15:30", "high"): {"n": 5, "model_mae": 0.5, "market_mae": 0.6,
          "disagreements": 2, "model_bin_wins": 1, "market_bin_wins": 1,
          "n_boundary": 3, "flat_rmse": 0.75, "live_rmse": 0.4,
          "flip_toward": 2, "flip_away": 0}}
    out = str(tmp_path / "edge")
    paths = edge_report.write_report(m, out)
    assert os.path.exists(os.path.join(out, "metrics.csv"))
    assert os.path.exists(os.path.join(out, "ASSESSMENT.md"))
    body = open(os.path.join(out, "metrics.csv")).read()
    assert "15:30" in body and "0.4" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_edge_report.py -k write_report -v`
Expected: FAIL — `AttributeError: module 'edge_report' has no attribute 'write_report'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to edge_report.py
import csv
import os

_COLS = ["capture_slot", "variable", "n", "model_mae", "market_mae",
         "disagreements", "model_bin_wins", "market_bin_wins", "n_boundary",
         "flat_rmse", "live_rmse", "flip_toward", "flip_away"]


def write_report(metrics_by_key: dict, out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "metrics.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(_COLS)
        for (slot, variable), m in sorted(metrics_by_key.items()):
            w.writerow([slot, variable] + [m.get(c) for c in _COLS[2:]])

    md_path = os.path.join(out_dir, "ASSESSMENT.md")
    lines = ["# Betting-time edge report", "",
             "Model-vs-market and flat-vs-live settlement-offset, by capture slot.",
             "Q2 (flat vs live RMSE) is measured on high rows only.", ""]
    for (slot, variable), m in sorted(metrics_by_key.items()):
        lines.append(f"## {slot} — {variable} (n={m['n']}, boundary days={m['n_boundary']})")
        lines.append(f"- Model MAE {m['model_mae']} vs Market MAE {m['market_mae']}")
        lines.append(f"- Disagreements {m['disagreements']}: model won {m['model_bin_wins']}, "
                     f"market won {m['market_bin_wins']}")
        if m.get("live_rmse") is not None:
            verdict = ("live gap BEATS flat" if (m["flat_rmse"] or 0) - (m["live_rmse"] or 0) >= 0.15
                       else "no clear offset edge")
            lines.append(f"- Offset: flat RMSE {m['flat_rmse']} vs live RMSE {m['live_rmse']} "
                         f"({verdict}); bin flips toward {m['flip_toward']} / away {m['flip_away']}")
        lines.append("")
    with open(md_path, "w") as fh:
        fh.write("\n".join(lines))
    return [csv_path, md_path]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_edge_report.py -k write_report -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add edge_report.py tests/test_edge_report.py
git commit -m "feat: edge-report writer (metrics.csv + ASSESSMENT.md)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: CLI entry + one-shot retro on existing data

**Files:**
- Modify: `edge_report.py` (add `run` + `__main__`)
- Test: `tests/test_edge_report.py`

**Interfaces:**
- Consumes: `betting_log.load`, `settlements.as_map`, all of the above.
- Produces: `run(betting_rows: list[dict], out_dir: str) -> list[str]` (join → metrics → write, end to end); a `__main__` block that loads the live `betting_log.jsonl`, builds `cli_map`/`hourly_map` from `settlements.as_map`, and writes to `docs/benchmarks/<today>/edge/`. Retro flag `--retro` instead loads `forecast_log.jsonl` same-day-high rows (mapped into the betting-row shape via `flat_offset`/`live_gap=None` unavailable → offset stats degrade to None) and writes to `docs/benchmarks/<today>/edge-retro/` with a directional-only banner.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_edge_report.py
def test_run_end_to_end(tmp_path, monkeypatch):
    rows = [_hi("15:30", 98.0, 96.9, [[None, 96, 0.2], [97, 98, 0.8]], 98.0, 97.0, 1.2)]
    # settlement maps come in via join inside run(); monkeypatch as_map through a shim
    monkeypatch.setattr(edge_report, "_settlement_maps",
                        lambda: ({date(2026, 7, 1): (98.0, 79.0)},
                                 {date(2026, 7, 1): (97.0, 79.0)}))
    rows[0]["target_date"] = "2026-07-01"
    out = str(tmp_path / "edge")
    paths = edge_report.run(rows, out)
    assert any(p.endswith("metrics.csv") for p in paths)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_edge_report.py -k end_to_end -v`
Expected: FAIL — `AttributeError: module 'edge_report' has no attribute 'run'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to edge_report.py
def _settlement_maps():
    import settlements
    return settlements.as_map("cli"), settlements.as_map("hourly")


def run(betting_rows: list[dict], out_dir: str) -> list[str]:
    cli_map, hourly_map = _settlement_maps()
    joined = join(betting_rows, cli_map, hourly_map)
    return write_report(metrics(joined), out_dir)


if __name__ == "__main__":
    import sys
    from datetime import date
    import betting_log
    today = date.today().isoformat()
    if "--retro" in sys.argv:
        import forecast_log
        rows = []
        for r in forecast_log.load(forecast_log._PATH):
            if r.get("variable") == "high" and r.get("lead_bucket") == 0 and r.get("market"):
                rows.append({"target_date": r["target_date"], "variable": "high",
                             "capture_slot": "retro", "cli_consensus": r.get("consensus"),
                             "market_ev": r["market"].get("ev"),
                             "market_buckets": r["market"].get("buckets"),
                             "model_bins": [], "flat_offset": 0.89, "live_gap": None})
        out = f"docs/benchmarks/{today}/edge-retro"
        paths = run(rows, out)
        print(f"RETRO (directional only, n={len(rows)}): {paths}")
    else:
        rows = betting_log.load()
        out = f"docs/benchmarks/{today}/edge"
        print(run(rows, out))
```

- [ ] **Step 4: Run test to verify it passes; then run the retro for real**

Run: `pytest tests/test_edge_report.py -v && python edge_report.py --retro`
Expected: tests PASS; retro prints a path and writes `docs/benchmarks/<today>/edge-retro/` (labeled directional, small n).

- [ ] **Step 5: Commit**

```bash
git add edge_report.py tests/test_edge_report.py docs/benchmarks
git commit -m "feat: edge-report CLI + one-shot retro on existing log

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Run the whole suite: `pytest -q` → all green.
- [ ] Dry-run the live capture inside a slot window (or force `now`): `python betting_log.py` prints `betting capture: slot=<one of the five>` when run 15:00-17:00 CDT, `slot=None` otherwise, and appends a well-formed row to `betting_log.jsonl`.
- [ ] Confirm `forecast_log.jsonl` is byte-unchanged by a betting capture (diff before/after).
- [ ] Confirm `.github/workflows/log.yml` restores and publishes `betting_log.jsonl` (grep for both lines).

---

## Post-merge follow-ups (land BEFORE the ~25-day decision gate, not needed for data capture)

These surfaced in the final whole-branch review. The **capture** is complete and correct now (all raw fields — `cli_consensus`, `live_gap`, `flat_offset`, market bins — are logged, so nothing is lost); these only extend the **report** math, which can be added any time before the numbers actually drive the offset decision.

1. **Boundary SLICE, not just count (Important). — DONE** (commit adds `metrics()` keyed by `(slot, variable, subset)` with subset in `all`/`boundary`/`mid_bin`; `_subset_metrics` helper; `_COLS` gains a `subset` column; ASSESSMENT leads with the BOUNDARY line. Tests: `test_metrics_slices_by_boundary`.) The decision gate can now read the boundary subset directly.

2. **Persist the directional caveat in the retro ASSESSMENT.md (Minor). — still open.** The `--retro` "directional only / small-n" banner prints to stdout only; the persisted file conveys small-n via the `n=` header and the `retro` slot label. A one-line caveat written into the retro's `ASSESSMENT.md` would make the artifact self-describing. Needs a small flag plumbed through `run`/`write_report`.

3. **Round floats on write (Minor). — DONE** (`_mae`/`_rmse` now `round(..., 4)`; retro artifact reads `0.4` not `0.3999999999999986`. Test: `test_metrics_values_are_rounded`.)
