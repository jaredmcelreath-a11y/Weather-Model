# Hardening Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three independent fixes: the Action stops recomputing calibration 96×/day and can never log unshifted rows as `basis="cli"`; regime flags latch across forecast_log upserts; a unanimous front undercut can't collapse sigma to observation-noise confidence.

**Architecture:** (1) `log.yml` restores/publishes `calibration.json` on the data branch; `calibration.get()`'s freshness moves from file mtime to the JSON's internal `computed` timestamp (restored files have mtime "now" — mtime freshness would freeze calibration forever), tolerates corrupt/empty files, and falls back to a stale cached copy when recompute fails; `scheduled_log.main()` gains an early guard that skips ALL model logging (but still records settlements) when calibration is unavailable. (2) `forecast_log.record`'s upsert ORs the old row's flags into the replacement. (3) `model.predict_variable` floors sigma at `FRONT_SIGMA_MIN` while `front_widened`. Spec: `docs/superpowers/specs/2026-07-13-hardening-batch-design.md`.

**Tech Stack:** Pure Python stdlib, GitHub Actions YAML, pytest via repo venv.

## Global Constraints

- Python 3.9 venv: run everything with `.venv/bin/python` from the repo root (`/Users/jared/Desktop/Weather Model`); no new dependencies.
- Branch: `hardening-batch` (already created; spec committed).
- `calibration.get()` semantics that must survive: `refresh=False` returns the cached copy even if stale (and None when no file); a fresh copy is returned without recomputing; recompute writes via `compute_and_save()`.
- New behavior: recompute failure returns the last cached copy (even stale) — `None` only when nothing usable exists at all.
- Guard rule: when `calibration.get()` yields no `settlement_offset`, scheduled_log must NOT call `model.snapshot` or write forecast/consensus/betting logs, but MUST still run `settlements.record()`.
- Front floor: exactly `FRONT_SIGMA_MIN = 1.5` (°F), applied as `sigma = max(sigma, FRONT_SIGMA_MIN)` only when `front_widened`; NO `live=` gating (must run in backtest, like the guard itself).
- Flag latch covers exactly `("convective_widened", "front_widened")`; the only-when-true key convention is unchanged.
- Calm-day model behavior byte-identical (the floor can't fire when the flag is False).
- Comment style: prose comments explaining why, matching each file's existing density.

---

### Task 1: `calibration.get()` — freshness by `computed`, corrupt tolerance, stale fallback

**Files:**
- Modify: `calibration.py` (`get()` at the end of the file, ~lines 661–676; add `_is_fresh` above it)
- Test: `tests/test_calibration_get.py` (new)

**Interfaces:**
- Consumes: existing `_PATH`, `_MAX_AGE`, `compute_and_save()`; `datetime`/`timedelta` already imported in calibration.py.
- Produces: `get(refresh: bool = True) -> dict | None` with the same signature; new helper `_is_fresh(cached: dict) -> bool`. Task 2's workflow relies on `get()` tolerating an empty/corrupt restored file.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calibration_get.py`:

```python
"""calibration.get() freshness/robustness: the scheduled Action restores
calibration.json from the data branch (mtime = 'just now' every run), so
freshness must travel with the file's internal `computed` timestamp; a failed
restore leaves an empty file, and a failed recompute should serve the last
good copy rather than nothing."""

import json
from datetime import datetime, timedelta

import calibration


def _write(path, payload):
    with open(path, "w") as fh:
        json.dump(payload, fh)


def _stamp(hours_ago):
    return (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _no_recompute(monkeypatch):
    def boom():
        raise AssertionError("must not recompute")
    monkeypatch.setattr(calibration, "compute_and_save", boom)


def test_fresh_by_computed_timestamp_skips_recompute(tmp_path, monkeypatch):
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"computed": _stamp(2), "bias": {}})
    _no_recompute(monkeypatch)
    assert calibration.get(refresh=True)["bias"] == {}


def test_stale_computed_recomputes_despite_fresh_mtime(tmp_path, monkeypatch):
    # The Action-restore scenario: file just written to disk (fresh mtime) but
    # its content says it was computed 30h ago -> must recompute.
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"computed": _stamp(30)})
    monkeypatch.setattr(calibration, "compute_and_save", lambda: {"computed": "new"})
    assert calibration.get(refresh=True) == {"computed": "new"}


def test_missing_computed_falls_back_to_mtime(tmp_path, monkeypatch):
    # Pre-upgrade file without a `computed` field, mtime just now -> fresh.
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"bias": {"low": 0.1}})
    _no_recompute(monkeypatch)
    assert calibration.get(refresh=True)["bias"] == {"low": 0.1}


def test_corrupt_file_recomputes(tmp_path, monkeypatch):
    # A failed `git show ... > calibration.json || true` leaves an EMPTY file;
    # get() must treat it as absent, not crash on json.load.
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    open(p, "w").close()
    monkeypatch.setattr(calibration, "compute_and_save", lambda: {"computed": "new"})
    assert calibration.get(refresh=True) == {"computed": "new"}


def test_recompute_failure_serves_stale_cache(tmp_path, monkeypatch):
    # Stale copy + dead upstream: a 2-day-old settlement offset beats none.
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"computed": _stamp(30), "settlement_offset": {"high": 0.9}})
    def boom():
        raise RuntimeError("IEM down")
    monkeypatch.setattr(calibration, "compute_and_save", boom)
    got = calibration.get(refresh=True)
    assert got["settlement_offset"] == {"high": 0.9}


def test_nothing_usable_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "_PATH", str(tmp_path / "missing.json"))
    def boom():
        raise RuntimeError("IEM down")
    monkeypatch.setattr(calibration, "compute_and_save", boom)
    assert calibration.get(refresh=True) is None


def test_refresh_false_returns_stale_cache_without_recompute(tmp_path, monkeypatch):
    p = str(tmp_path / "calibration.json")
    monkeypatch.setattr(calibration, "_PATH", p)
    _write(p, {"computed": _stamp(30), "bias": {}})
    _no_recompute(monkeypatch)
    assert calibration.get(refresh=False)["computed"]  # stale copy returned as-is
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_calibration_get.py -v`
Expected: `test_stale_computed_recomputes_despite_fresh_mtime` FAILS (mtime says fresh, stale copy returned), `test_corrupt_file_recomputes` FAILS (JSONDecodeError), `test_recompute_failure_serves_stale_cache` FAILS (returns None today). The others pass — they pin the semantics that must not change.

- [ ] **Step 3: Implement**

In `calibration.py`, replace the whole `get()` function with (and add `_is_fresh` above it):

```python
def _is_fresh(cached: dict) -> bool:
    """Freshness travels with the FILE CONTENT, not the file's mtime: the
    scheduled Action restores calibration.json from the data branch on every
    run, which resets mtime to 'just now' — mtime-based freshness would never
    recompute and silently freeze the calibration. The internal `computed`
    timestamp survives the round-trip; files without one (pre-upgrade) fall
    back to mtime. Timestamps are naive-local; a few hours' clock skew between
    local and CI is acceptable against the 24h TTL."""
    stamp = cached.get("computed")
    if stamp:
        try:
            age = datetime.now() - datetime.fromisoformat(stamp)
            return age.total_seconds() < _MAX_AGE
        except ValueError:
            pass
    return time.time() - os.path.getmtime(_PATH) < _MAX_AGE


def get(refresh: bool = True) -> dict | None:
    """Return cached calibration, recomputing if stale. A corrupt/empty file
    (e.g. a failed data-branch restore) reads as absent; a failed recompute
    serves the last cached copy even if stale (a 2-day-old settlement offset
    beats logging unshifted rows) — None only when nothing usable exists, so
    callers can treat None as 'no calibration at all'."""
    cached = None
    if os.path.exists(_PATH):
        try:
            with open(_PATH) as fh:
                cached = json.load(fh)
        except (json.JSONDecodeError, OSError):
            cached = None
    if cached is not None and (not refresh or _is_fresh(cached)):
        return cached
    if not refresh:
        return None
    try:
        return compute_and_save()
    except Exception:
        return cached
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_calibration_get.py -v` — 7/7 PASS.
Run: `.venv/bin/python -m pytest -q` — all pass (291 pre-existing + 7 new).

- [ ] **Step 5: Commit**

```bash
git add calibration.py tests/test_calibration_get.py
git commit -m "feat: calibration freshness travels with the file + stale-beats-nothing fallback"
```

---

### Task 2: scheduled_log guard + workflow persistence

**Files:**
- Modify: `scheduled_log.py` (restructure `main()`)
- Modify: `.github/workflows/log.yml` (restore + publish steps)
- Test: `tests/test_scheduled_log_guard.py` (new)

**Interfaces:**
- Consumes: Task 1's `get()` contract — None only when nothing usable exists.
- Produces: `scheduled_log.main()` (same entrypoint); helpers `_log_snapshots(calib, off) -> None` and `_record_settlements() -> int`. No other module consumes these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduled_log_guard.py`:

```python
"""The Action's calibration guard: without a settlement offset the snapshot is
hourly-basis numbers, and logging them as basis="cli" silently poisons the
scoring cohort — so scheduled_log must skip ALL model logging (but still record
settlements, which need no calibration)."""

import betting_log
import calibration
import consensus_log
import forecast_log
import model
import scheduled_log
import settlements
from sources import kalshi


def test_main_skips_model_logging_without_calibration(monkeypatch, capsys):
    monkeypatch.setattr(calibration, "get", lambda refresh=True: None)
    def boom(*a, **k):
        raise AssertionError("model.snapshot must not run without calibration")
    monkeypatch.setattr(model, "snapshot", boom)
    called = {}
    monkeypatch.setattr(settlements, "record", lambda: called.setdefault("rec", True))
    monkeypatch.setattr(settlements, "load", lambda path=None: [])
    scheduled_log.main()
    assert called.get("rec") is True
    assert "skipping model logging" in capsys.readouterr().out


def test_main_logs_when_calibration_present(monkeypatch):
    calib = {"settlement_offset": {"high": 0.9, "low": -0.4},
             "computed": "2026-07-13T10:00:00"}
    snap = {"updated": "2026-07-13T10:00:00",
            "today": {"day": "2026-07-13"}, "tomorrow": {"day": "2026-07-14"}}
    seen = []
    monkeypatch.setattr(calibration, "get", lambda refresh=True: calib)
    monkeypatch.setattr(model, "snapshot",
                        lambda c, settle_offset=None, continuous_obs=False: snap)
    monkeypatch.setattr(kalshi, "implied_block", lambda t, tm: {})
    monkeypatch.setattr(forecast_log, "record",
                        lambda s, path=None, basis="hourly": seen.append(("forecast", basis)))
    monkeypatch.setattr(consensus_log, "record",
                        lambda s, path=None, basis="hourly": seen.append(("consensus", basis)))
    monkeypatch.setattr(betting_log, "current_slot", lambda now, **k: None)
    monkeypatch.setattr(settlements, "record", lambda: seen.append(("settlements",)))
    monkeypatch.setattr(settlements, "load", lambda path=None: [])
    monkeypatch.setattr(forecast_log, "load", lambda path=None: [])
    scheduled_log.main()
    assert ("forecast", "cli") in seen
    assert ("consensus", "cli") in seen
    assert ("settlements",) in seen
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scheduled_log_guard.py -v`
Expected: the first test FAILS — today's `main()` calls `model.snapshot` even with `calib=None` (the AssertionError from `boom` surfaces).

- [ ] **Step 3: Restructure `scheduled_log.main()`**

Replace the body of `main()` (keep the module docstring and imports; note `from datetime import date` moves to module level with the other imports) with:

```python
from datetime import date, datetime


def _record_settlements() -> int:
    """Persist actual settlements for any settled forecast day — the one job
    that needs no calibration, so it runs even when the model logging is
    skipped. Best-effort: an archive hiccup just leaves days for the next run."""
    try:
        settlements.record()
    except Exception as e:
        print(f"settlement recording skipped: {e}")
    return len(settlements.load(settlements._PATH))


def _log_snapshots(calib: dict, off) -> None:
    """The model-logging body of a scheduled run: CLI snapshot + market block +
    forecast/consensus logs + the slot-gated betting capture."""
    cli_snap = model.snapshot(calib, settle_offset=off, continuous_obs=True)
    # Attach the live Kalshi market's implied forecast so the log can later
    # score market-vs-model against settlement. Best-effort: a market outage
    # just omits the block and the model logging is unaffected.
    try:
        today = date.fromisoformat(cli_snap["today"]["day"])
        tomorrow = date.fromisoformat(cli_snap["tomorrow"]["day"])
        cli_snap["market"] = kalshi.implied_block(today, tomorrow)
    except Exception as e:
        print(f"market block skipped: {e}")
    forecast_log.record(cli_snap, basis="cli")
    consensus_log.record(cli_snap, basis="cli")
    # Betting-time capture: only when `now` falls in a betting slot (5x/day).
    # Best-effort: an error here doesn't block the logging above.
    try:
        from betting_log import TZ as _BTZ
        if betting_log.current_slot(datetime.now(_BTZ)) is not None:
            hourly_snap = model.snapshot(calib)
            slot = betting_log.capture_if_slot(cli_snap, hourly_snap, calib)
            print(f"betting-time capture at slot {slot}")
    except Exception as e:
        print(f"betting capture skipped: {e}")


def main() -> None:
    calib = calibration.get(refresh=True)
    off = (calib or {}).get("settlement_offset")
    if off is None:
        # No calibration at all (recompute failed AND no cached copy — a >24h
        # sustained outage): the snapshot would be hourly-basis numbers, and
        # logging them as basis="cli" would silently poison the scoring cohort.
        # Skip ALL model logging this run; settlements need no calibration.
        print("calibration unavailable — skipping model logging (settlements only)")
        s = _record_settlements()
        print(f"settlements log holds {s} records")
        return
    print(f"calibration: using copy computed {calib.get('computed', 'unknown')}")
    _log_snapshots(calib, off)
    s = _record_settlements()
    n = len(forecast_log.load(forecast_log._PATH))
    print(f"logged cli snapshot; log now holds {n} records, {s} settlements")
```

(The old inline `try: settlements.record() ...` block and the old body are fully replaced; the retired comment about the hourly basis stays in the module docstring.)

- [ ] **Step 4: Edit `.github/workflows/log.yml`**

4a. In the "Restore existing logs from the data branch" step, add after the `betting_log.jsonl` line:

```yaml
            git show origin/data:calibration.json > calibration.json 2>/dev/null || true
```

(A failed restore leaves an empty file; `calibration.get()` now tolerates that — Task 1.)

4b. In the "Publish the logs to the data branch" step, add after the `betting_log.jsonl` cp line:

```yaml
          [ -f calibration.json ] && [ -s calibration.json ] && cp calibration.json "$tmp/calibration.json"
```

and after the `git add -f betting_log.jsonl` line:

```yaml
          [ -f calibration.json ] && git add -f calibration.json
```

(Same `[ -f ] &&` idiom the step already uses — safe under `set -e` because the guard test is not the final command in the `&&` list. `-f` because calibration.json is gitignored. Also update the workflow's top comment sentence "(just those files)" if it enumerates them — keep the comment accurate.)

- [ ] **Step 5: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_scheduled_log_guard.py -v` — 2/2 PASS.
Run: `.venv/bin/python -m pytest -q` — all pass.
Sanity-parse the YAML: `.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/log.yml')); print('yaml ok')"` (PyYAML ships with the venv via streamlit deps; if it's absent, eyeball the indentation instead).

- [ ] **Step 6: Commit**

```bash
git add scheduled_log.py .github/workflows/log.yml tests/test_scheduled_log_guard.py
git commit -m "feat: persist calibration on the data branch + guard the Action's CLI logging"
```

---

### Task 3: Flag latch across forecast_log upserts

**Files:**
- Modify: `forecast_log.py` (the upsert loop at the end of `record()`, ~lines 167–174)
- Test: `tests/test_accuracy.py` (one test after `test_forecast_log_stamps_regime_flags`)

**Interfaces:**
- Consumes: the flag stamping shipped in d8f828c (`convective_widened`/`front_widened`, only-when-true).
- Produces: upsert semantics — a day flagged at ANY capture stays flagged. No API change.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_accuracy.py`:

```python
def test_forecast_log_flag_latches_across_upserts(tmp_path):
    p = str(tmp_path / "log.jsonl")
    # 3pm capture: the front guard is firing on today's low.
    snap = _snapshot(datetime(2026, 6, 16, 15, tzinfo=TZ))
    snap["today"]["low"]["front_widened"] = True
    forecast_log.record(snap, path=p)
    # 10pm capture: the storm passed, the guard un-fired — but the day WAS a
    # regime day, so the flag must latch (the correction pool and the exclusion
    # count key on "fired at any point today").
    forecast_log.record(_snapshot(datetime(2026, 6, 16, 22, tzinfo=TZ)), path=p)
    rows = {(r["target_date"], r["variable"]): r for r in forecast_log.load(p)}
    low = rows[(TODAY.isoformat(), "low")]
    assert low["front_widened"] is True                 # latched
    assert low["captured_at"].startswith("2026-06-16T22")  # still the latest capture
    assert "front_widened" not in rows[(TODAY.isoformat(), "high")]  # never-flagged stays clean
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_accuracy.py::test_forecast_log_flag_latches_across_upserts -v`
Expected: FAIL — the 10pm upsert overwrites the row, dropping the flag.

- [ ] **Step 3: Implement**

In `forecast_log.py`, the upsert loop currently reads:

```python
    for rec in new_recs:
        k = _key(rec)
        if k in index:
            rows[index[k]] = rec
        else:
            index[k] = len(rows)
            rows.append(rec)
```

Change to:

```python
    for rec in new_recs:
        k = _key(rec)
        if k in index:
            # Latch regime flags across upserts: a day the guard fired on at
            # ANY capture stays flagged even if the storm passed before this
            # final capture un-fired it — the correction pool excludes by
            # "was this a regime day", not "was the guard firing at 11:45pm".
            for flag in ("convective_widened", "front_widened"):
                if rows[index[k]].get(flag):
                    rec[flag] = True
            rows[index[k]] = rec
        else:
            index[k] = len(rows)
            rows.append(rec)
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_accuracy.py -q` — all pass (including the Task-1-era flag tests).
Run: `.venv/bin/python -m pytest -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add forecast_log.py tests/test_accuracy.py
git commit -m "fix: regime flags latch across forecast_log upserts"
```

---

### Task 4: Front sigma floor

**Files:**
- Modify: `config.py` (after `FRONT_SCAN_FROM_HOUR`)
- Modify: `model.py` (config import; `predict_variable` after the convective-floor block, before `probs = _bin_probabilities(...)`)
- Test: `tests/test_front_guard.py` (one test appended)

**Interfaces:**
- Consumes: `front_widened` (computed earlier in `predict_variable`, True only on a locked low with ≥1 undercutting member).
- Produces: `config.FRONT_SIGMA_MIN = 1.5`. Display-side nothing changes (`sigma_used` already shown).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_front_guard.py` (uses the existing `_fc`, `_curve`, `_at`, `_obs_locked_afternoon` helpers):

```python
def test_unanimous_undercut_floors_sigma():
    # BOTH members project the same evening undercut: the sample spread
    # collapses, but the projected new low is still an hours-ahead forecast —
    # sigma must floor at FRONT_SIGMA_MIN instead of printing observation-noise
    # confidence (the May 5 replay: sigma 0.8 on a 3.2°F miss).
    from config import FRONT_SIGMA_MIN
    ev = {18: 80, 21: 76, 23: 74}
    series = {"det_a": _fc(_curve(ev)), "det_b": _fc(_curve(ev))}
    out = model.predict_variable(series, {"obs": _obs_locked_afternoon()},
                                 _DAY, "low", _at(14), None)
    assert out["front_widened"] is True
    assert out["sigma_used"] >= FRONT_SIGMA_MIN
```

(The calm-day counterpart already exists: `test_predict_variable_calm_day_unchanged` asserts `sigma_used == model._SIGMA_FLOOR`, which proves the floor can't fire when the flag is False.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_front_guard.py::test_unanimous_undercut_floors_sigma -v`
Expected: FAIL — unanimous samples collapse `locked_ratio` to 0 and sigma prints `_SIGMA_FLOOR` (0.7).

- [ ] **Step 3: Implement**

3a. `config.py`, directly after `FRONT_SCAN_FROM_HOUR = 12`:

```python
FRONT_SIGMA_MIN = 1.5   # °F; sigma floor while the front guard holds a locked
                        # low open. A projected-but-unrealized evening event
                        # deserves at least this much spread (the same idiom and
                        # value as CONVECTIVE_SIGMA_MIN) — even when every member
                        # agrees on the undercut and the raw sample spread
                        # collapses, the projection is still hours ahead.
```

3b. `model.py` — add `FRONT_SIGMA_MIN` to the config import (alphabetical, after `FRONT_SCAN_FROM_HOUR`):

```python
from config import (BIN_HIGH, BIN_LOW, CACHE_TTL_SECONDS, CALM_WIND_MAX,
                    CLEAR_CLOUD_MAX, FRONT_SCAN_FROM_HOUR, FRONT_SIGMA_MIN,
                    FRONT_UNDERCUT_MARGIN, HIGH_BUMPY_STD, HIGH_LOCK_DROP,
                    HIGH_LOCK_NOON_OFFSET_HOURS, HIGH_PLATEAU_MAX,
                    LEAD_SIGMA_INFLATION, LOW_LOCK_RISE, MAX_CLI_GAP,
                    PEAK_LOCK_DROP, TIMEZONE, bin_labels, lead_bucket)
```

3c. `model.py`, in `predict_variable`, directly after the convective-floor block (`if live and variable == "low" ...` ending with `except Exception: pass`) and before `probs = _bin_probabilities(samples, sigma, weights)`:

```python
    # Front-guard humility: when members project an evening undercut of the
    # locked low, the projected new low is still an hours-ahead forecast. If
    # every member agrees, the sample spread collapses and sigma would print
    # observation-noise confidence on an unrealized event (May 5 replay: sigma
    # 0.8 on a 3.2°F miss). Floor it like the convective case; the hard bound
    # keeps the widening one-sided. Flag-driven (no live data), so unlike the
    # convective floor it also runs in backtest — exactly like the guard.
    if front_widened:
        sigma = max(sigma, FRONT_SIGMA_MIN)
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_front_guard.py -v` — all pass, including the pre-existing `test_predict_variable_front_day_shifts_and_widens` (its member disagreement already yields sigma 2.6 > 1.5) and `test_predict_variable_calm_day_unchanged` (flag False → floor inert).
Run: `.venv/bin/python -m pytest -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add config.py model.py tests/test_front_guard.py
git commit -m "feat: FRONT_SIGMA_MIN floor — a unanimous front projection is still a forecast"
```

---

### Task 5: Final verification

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (291 pre-existing + 11 new = 302).

- [ ] **Step 2: Local end-to-end smoke of the guard's happy path**

Run: `.venv/bin/python -c "
import calibration
c = calibration.get(refresh=True)
print('calib computed:', (c or {}).get('computed'), '| offset present:', (c or {}).get('settlement_offset') is not None)"`
Expected: prints a timestamp and `offset present: True` (uses the local cached file or recomputes).

- [ ] **Step 3: Post-merge (note for the controller, not a code step)**

After merge+push, watch the next Action run's log for `calibration: using copy computed …` and confirm `calibration.json` appears on the `data` branch; the run after THAT should reuse it (same `computed` stamp, no recompute delay).

- [ ] **Step 4: Use superpowers:finishing-a-development-branch to merge/PR `hardening-batch`**
