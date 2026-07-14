# Hardening batch — design

**Date:** 2026-07-13
**Status:** approved design, pending implementation plan

Three independent fixes from today's reviews, shipped as one branch:

1. the Action's calibration failure path (audit roadmap item 3),
2. the flag-latch-on-upsert spec gap (storm-proof-corrections final review, Important #2),
3. the front sigma floor (front-guard final review, Important #1).

## Problem

1. **Calibration in the Action.** `scheduled_log.py` runs `calibration.get(refresh=True)`
   on a fresh VM every 15 minutes: `calibration.json` is gitignored and never
   restored, so every run recomputes 45 days of IEM + Open-Meteo archives
   (~96 heavy pulls/day). Worse, when that recompute fails, `calib` is `None`,
   `settlement_offset` is `None`, and the snapshot is **still logged with
   `basis="cli"` despite being unshifted hourly-basis numbers** — one IEM outage
   silently poisons the CLI scoring cohort.
2. **Flag latch.** `forecast_log.record` upserts keep only the LAST capture per
   (target_date, variable, lead_bucket, basis). A storm that passes before the
   day's final Action run un-fires the regime guards, so the final record lands
   unflagged, re-enters the correction pool, and under-counts the dashboard's
   exclusion note.
3. **Front floor.** When ALL members agree on a front undercut, the locked low's
   sample spread collapses and sigma prints the 0.7 observation-noise floor on
   what is still an hours-ahead forecast (May 5 replay: consensus 65.2, sigma
   0.8, settled 62.0 — a confident 3.2°F miss). The badge protects the human;
   the bins still carry the false confidence.

## Decision summary

| Question | Decision |
|---|---|
| Calib-unavailable behavior | **Skip model logging, keep settlements** (no tagged rows) |
| Front floor sizing | **Constant `FRONT_SIGMA_MIN = 1.5`**, convective-floor idiom, no live gate |

## Mechanism

### 1a. Workflow: persist calibration.json on the data branch

`.github/workflows/log.yml`:

- **Restore step**: alongside the existing log restores, add
  `git show origin/data:calibration.json > calibration.json || true`.
- **Publish step**: copy `calibration.json` into the temp publish dir and
  `git add -f` it (it is gitignored) alongside the other files, when present.

### 1b. `calibration.get()`: freshness travels with the file

Freshness currently uses file mtime — but a restored file's mtime is "just now"
every run, so the Action would **never** recompute and calibration would silently
freeze. Change: freshness = the JSON's internal `computed` timestamp
(`datetime.now() - fromisoformat(computed) < _MAX_AGE`), falling back to mtime
when `computed` is missing/unparsable (old files). Timestamps are naive-local;
worst-case few-hour skew between local and CI clocks on a 24h TTL is accepted.

Effect: the Action recomputes roughly once a day (when the restored copy goes
stale) instead of 96×; an IEM outage inside the 24h window is fully absorbed by
the restored copy.

### 1c. `scheduled_log.py`: guard the CLI logging

Early guard after `calib = calibration.get(refresh=True)`:

- If `calib` is `None` (recompute failed AND no usable restored copy — now only
  reachable after a >24h sustained outage): print the reason, **skip the CLI
  snapshot and all model logging** (forecast_log, consensus_log, betting_log),
  and **still run `settlements.record()`** — settlements need no calibration.
  Cost: one 15-min sample during a genuine sustained outage; the upsert design
  makes single-sample gaps invisible.
- Observability: print one line stating which path ran —
  `calibration: reused cached copy` / `calibration: recomputed` /
  `calibration unavailable — skipping model logging (settlements only)`.
  (Distinguishing reused/recomputed: `get()` result's `computed` field is
  older/newer than process start, or expose it via a tiny helper — implementer's
  choice, behavior is what matters.)

### 2. Flag latch on upsert (`forecast_log.record`)

In the upsert loop, when a new record replaces an existing row, OR the old row's
regime flags into the replacement:

```python
for flag in ("convective_widened", "front_widened"):
    if rows[index[k]].get(flag):
        rec[flag] = True
```

(placed before the assignment that overwrites the row). Semantics become "the
guard fired at any capture today," which is what the correction-pool exclusion
wants. The only-when-true key convention is unchanged.

### 3. Front sigma floor (`model.py` + `config.py`)

- `config.py`: `FRONT_SIGMA_MIN = 1.5  # °F` with a comment tying it to
  `CONVECTIVE_SIGMA_MIN` (same idiom: a projected-but-unrealized evening event
  deserves at least this much spread).
- `model.predict_variable`: directly after the convective-floor block, when
  `front_widened` is true: `sigma = max(sigma, FRONT_SIGMA_MIN)`.
  - Flag-driven — no `live=` gate, runs identically in backtest/replay
    (deliberately unlike the convective floor, exactly like the guard itself).
  - One-sided in effect: the hard bound already deletes mass above the observed
    min, so widening pushes mass down.
  - Calm days can't hit it: `front_widened` is False by construction.
  - Order note: applied after the settle-gap quadrature widening and the
    convective floor; all are max/hypot compositions, so ordering with the
    convective max is immaterial.

## Interactions verified

- **Streamlit deploy**: unaffected by 1a/1c (no `calibration.json` in its
  checkout → recomputes as today; `get()`'s new freshness reads its own written
  file's `computed`, same behavior). The dashboard never writes logs on cloud.
- **Local runs**: `calibration.json` exists with `computed`; new freshness is
  behaviorally identical (mtime ≈ computed for locally written files).
- **Correction estimators**: the latch only ever ADDS flags → exclusions can
  only grow; no scoring code change needed.
- **Front-guard tests**: `test_predict_variable_front_day_shifts_and_widens`
  asserts `sigma_used > _SIGMA_FLOOR` and its member disagreement already
  yields sigma 2.6 — unaffected by a 1.5 floor. The calm-day test asserts
  `sigma_used == _SIGMA_FLOOR` — unaffected (flag False).

## Testing

1. Freshness: a calib file with `computed` 2h ago → `get(refresh=True)` returns
   it without recomputing (monkeypatched `compute` raises if called); `computed`
   30h ago → recompute runs. Missing `computed` → falls back to mtime behavior.
2. scheduled_log guard: monkeypatch `calibration.get` → None; `model.snapshot`
   must never be called; `settlements.record` must still be called. (Restructure
   `main()` minimally if needed for testability — e.g. extract the logging body
   into a helper `main()` calls after the guard.)
3. Latch: record a snapshot with `front_widened` True, then upsert the same
   (date, variable, lead) with the flag absent → the stored row keeps
   `front_widened: true`. A never-flagged row stays flag-free.
4. Floor: front day with BOTH members undercutting to the same value (tight
   agreement — old behavior collapsed sigma to `_SIGMA_FLOOR`) →
   `sigma_used >= 1.5` and consensus at the undercut; calm locked day →
   byte-identical to today (`sigma_used == _SIGMA_FLOOR`, no floor applied).
5. Workflow yml: no automated test — verified post-merge by watching the next
   Action run's log for the new calibration print line and confirming
   `calibration.json` appears on the data branch.

## Out of scope

- Robustifying the archive-based warm-low/cooling estimators.
- Any change to the front guard's trigger (margin recalibration waits for data).
- Dashboard changes (the exclusion note already ships; the latch only makes its
  count more honest).
