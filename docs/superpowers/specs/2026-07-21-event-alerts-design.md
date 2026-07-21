# Event Alerts (Storm / Front / Morning Recap) — Design

**Date:** 2026-07-21
**Status:** Approved, ready for planning

## Goal

Add three push-only ntfy alerts, fired from the every-10-min scheduled run, each
**once per day**, with title-case titles matching the existing alerts:

1. **Storm Watch Active** — an upstream severe-storm risk to today's low.
2. **Front Risk** — a forecast cold front projecting a colder evening low.
3. **Morning Recap** — a daily 6:30 AM digest of yesterday's result + today's setup.

Storm and Front are **event-triggered** (fire the first time today's condition is
true). Morning Recap is **time-triggered** (first run at/after 6:30 AM local).

## Channel & reuse

ntfy push only, reusing the `NTFY_TOPIC` secret and `notify.send_ntfy`. Shares the
once-per-day state pattern already used by the CLI and resolved alerts.

## Triggers, titles, bodies

### 1. Storm Watch Active
- **Trigger:** `snap["storm"]["level"] == "active"` (upstream severe-thunderstorm
  warning matched, or convective sigma ≥ full floor). *Not* `"watch"` — active only.
- **Key day:** today's climate day (`settlement.climate_day_of(now)`).
- **Title:** `Storm Watch Active`
- **Body:** if `storm["upstream"]["active"]`:
  `SVR warning {county} Co ({direction}) · low downside ±{sigma:g}°F`
  else: `Convective storms on the approach · low downside ±{sigma:g}°F`.
  (`storm["sigma"]` is the one-sided downside °F; `county`/`direction` from
  `storm["upstream"]`.)

### 2. Front Risk
- **Trigger:** `snap["today"]["low"]["front_widened"]` is truthy.
- **Key day:** today's climate day.
- **Title:** `Front Risk`
- **Body:** `Front may undercut tonight's low · projection ≈{proj:g}°F`
  where `proj = snap["today"]["low"]["front_guard"]["projection"]`. If
  `front_guard` is missing, fall back to the low consensus.

### 3. Morning Recap
- **Trigger:** local time (America/Chicago) ≥ 06:30 **and** not yet sent today.
- **Key day:** local calendar date (`now` in `TIMEZONE`), since it's a
  start-of-day digest (at 06:30 the local date equals the climate day anyway).
- **Title:** `Morning Recap`
- **Body:** compact, up to two lines built from `recap.today_setup(snap)` and
  `recap.yesterday_scorecard(...)`:
  - Yesterday line (omitted until yesterday has settled):
    `Yesterday: High {settled:g} (model {model:g}, {Exact ✓ | Miss ±n}); `
    `Low {settled:g} ({Exact ✓ | Miss ±n})`.
    "Miss ±n" uses `settled - model` (matches the card's convention).
  - Today line (always):
    `Today: Low ~{observed:g} ({Locked|Developing}), High ~{consensus:g}`.
  - Join with a newline. If `today_setup` is unavailable, skip the recap entirely.

## Architecture

### New module `alerts.py`

Houses all alert **state I/O** and the **new** alert logic, keeping `scheduled_log`
lean. Pure builders are unit-testable without network or Streamlit.

```
load_state(path: str) -> dict          # moved from scheduled_log._load_alert_state
save_state(path: str, state: dict)     # json.dump wrapper

storm_body(storm: dict) -> str         # pure message builder
front_body(low: dict) -> str           # pure message builder
recap_body(setup: dict, yesterday: dict | None) -> str  # pure, "" if setup falsy

maybe_fire_events(snap: dict, now: datetime) -> None
    # orchestrator: checks storm/front/recap, gates once-per-day via
    # EVENT_STATE_PATH, sends via notify.send_ntfy, records the day. Best-effort.
```

- `EVENT_STATE_PATH` = `event_alert_state.json` next to the module.
- `RECAP_HOUR, RECAP_MINUTE = 6, 30`.
- `maybe_fire_events` loads the shared state `{"storm": day, "front": day,
  "recap": day}`, evaluates each trigger, and for each that fires and isn't
  already recorded for its key-day, calls `notify.send_ntfy(title, body)`; on a
  successful send it records the day and marks dirty. Writes state once at the
  end if dirty. Each alert is independently wrapped so one failure can't block
  the others; the whole function is also wrapped best-effort.
- Recap data is built inside `maybe_fire_events` (needs `snap`, `settlements`,
  `forecast_log`), mirroring `app.load_recap`:
  `recap.yesterday_scorecard(date.today(), settlements.as_map("cli"),
  forecast_log.load(), bet_rows=<best-effort or None>)` and
  `recap.today_setup(snap)`. A failure here yields no recap, never raises.

### `scheduled_log.py`

- `_load_alert_state` is replaced by `alerts.load_state` (import it; keep the
  CLI/resolved alerts working by pointing them at `alerts.load_state`).
- In `_log_snapshots`, after `_maybe_alert_resolved(cli_snap, now)`:
  `alerts.maybe_fire_events(cli_snap, now)`.

### State — `event_alert_state.json` on the `data` branch

One combined file for all three new alerts, keyed by alert name → last-fired day
(ISO). Tolerates the 0-byte restore artifact via `alerts.load_state` (same rule
as the other state files).

### Workflow — `.github/workflows/log.yml`

- **Restore:** add
  `git show origin/data:event_alert_state.json > event_alert_state.json 2>/dev/null || true`.
- **Publish:** add the `cp` + `git add -f` lines for `event_alert_state.json`.
- `NTFY_TOPIC` env already present — no change.

## Data flow

Each 10-min run builds `cli_snap`, then `alerts.maybe_fire_events` checks the
three triggers. Storm/Front fire the first run their condition holds that day;
Morning Recap fires the first run past 6:30 AM. Recorded days keep each quiet for
the rest of the day; all three re-arm next day.

## Error handling

Best-effort everywhere: a missing snapshot field, a recap-build failure, a send
failure, or a corrupt/empty state file skips that alert (logged) and never blocks
the other alerts or the surrounding forecast/consensus logging.

## Testing

- **`storm_body`**: with an upstream warning → includes county/direction + sigma;
  without → the "on the approach" phrasing + sigma.
- **`front_body`**: uses `front_guard.projection`; falls back to consensus when
  `front_guard` is absent.
- **`recap_body`**: yesterday+today when settled; today-only when `yesterday` is
  None; `""` when `setup` is falsy. Exact vs Miss ±n formatting.
- **`maybe_fire_events`** (with `notify.send_ntfy` mocked, `EVENT_STATE_PATH` →
  tmp file, `snap`/recap dicts hand-built or the recap build monkeypatched):
  - Storm fires on `active`, not on `watch`.
  - Front fires when `front_widened` truthy, not when false.
  - Recap fires at 06:30+ local, not at 06:00; not twice the same day; re-arms
    next day.
  - The three are independent (one firing doesn't gate another).
  - Empty state file doesn't block.
- **`alerts.load_state`**: missing/empty/corrupt file → `{}` (regression parity
  with the old `_load_alert_state`).

## Out of scope

- On-page elements (push only).
- Storm "watch"-level pings (active only).
- Betting/edge alerts.
- Migrating the existing CLI/resolved alerts into `alerts.py` beyond sharing
  `load_state` (a later cleanup, not this change).

## Decisions locked in

- Storm: **active only**. Morning Recap: **6:30 AM local**.
- Titles: `Storm Watch Active`, `Front Risk`, `Morning Recap`.
- Recap body: compact two-line (yesterday scorecard + today setup), yesterday
  omitted until settled.
