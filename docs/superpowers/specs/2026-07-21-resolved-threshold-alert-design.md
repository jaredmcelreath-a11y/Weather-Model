# Resolved-Threshold Alert — Design

**Date:** 2026-07-21
**Status:** Approved, ready for planning

## Goal

Send a push-only ntfy alert the first time each day that a variable's **displayed
Resolved %** reaches **70%** — one alert for the high, one for the low, fired
independently (the low usually resolves in the morning, the high in the
afternoon). This is an early "it's locking in" signal, complementing the
official CLI report alert that lands later (~4:41 PM).

## Channel

ntfy push only (reuses the existing `NTFY_TOPIC` secret + `notify.send_ntfy`).
No on-page element — the Resolved % is already shown live on the Forecast card,
so a persistent box would be redundant.

## Trigger

- Per variable `var` in `("high", "low")` for **today's climate day**:
  `pct = model.displayed_resolved(snap["today"][var])`.
- Fire when `pct >= RESOLVED_ALERT_PCT` (70) **and** that variable has not already
  been alerted today.
- `resolved` is monotonic (never climbs then drops), so a single crossing per day
  is enough — no re-arming within a day.

### Why displayed, not raw, resolved

`model.displayed_resolved(d)` is the exact number the metric card shows: the raw
`resolved` clamped to `CONVECTIVE_RESOLVED_CAP` (90) on a convective- or
front-risk day. Using it keeps the alert consistent with what the user sees and
avoids pinging "resolved" when a storm/front could still undercut the low. The
cap (90) is well above the 70 threshold, so a genuine crossing still fires.

## Refactor: move `displayed_resolved` into `model.py`

`displayed_resolved` and `CONVECTIVE_RESOLVED_CAP` currently live in
`market_view.py`, which imports Streamlit at module top. The scheduled cron
(`scheduled_log.py`) must not pull in the whole UI layer. Both symbols are pure,
so move them to `model.py` and have `market_view` re-import them:

- In `model.py`: add `CONVECTIVE_RESOLVED_CAP = 90` and `displayed_resolved(d)`
  (verbatim logic, including the `convective_widened` / `front_widened` cap).
- In `market_view.py`: delete the local definitions; add
  `from model import CONVECTIVE_RESOLVED_CAP, displayed_resolved` (module already
  imports `model`). The single call site (`locked_pct = displayed_resolved(d)`,
  ~line 1229) keeps working unchanged, as does any `market_view.displayed_resolved`
  reference via the re-import.

## Components

### `scheduled_log.py`

- New constant `RESOLVED_ALERT_PCT = 70`.
- New constant `RESOLVED_STATE_PATH = os.path.join(dir, "resolved_alert_state.json")`.
- New `_maybe_alert_resolved(snap: dict, now: datetime) -> None`:
  1. `today = settlement.climate_day_of(now)`.
  2. Load state (dict `{var: last_alerted_day_iso}`), tolerating an empty/corrupt
     file exactly like `_maybe_alert_cli` (0-byte restore file → `{}`).
  3. For each `var` in `("high", "low")`:
     - `d = snap.get("today", {}).get(var)`; skip if missing.
     - `pct = model.displayed_resolved(d)`.
     - If `pct < RESOLVED_ALERT_PCT` → skip.
     - If `state.get(var) == today.isoformat()` → already alerted, skip.
     - `title = f"Dallas {var.capitalize()} locking in"`,
       `body = f"{pct}% resolved · ≈{d['consensus']:g}°F"`.
     - If `notify.send_ntfy(title, body)`: set `state[var] = today.isoformat()`,
       mark dirty, print `f"Resolved alert sent: {var} {pct}%"`.
       Else print the send-failed line.
  4. If any state changed, write the whole state dict back to
     `RESOLVED_STATE_PATH`.
  5. Whole body wrapped best-effort; a failure logs `f"Resolved alert skipped: {e}"`
     and never blocks the surrounding logging.
- Call `_maybe_alert_resolved(cli_snap, now)` from `_log_snapshots`, right after
  `cli_snap` is built (it already has `now = datetime.now(model.TZ)` and the
  snapshot). Placed there (not in `main`) because it needs the model snapshot,
  which only exists when calibration is available — the same precondition as the
  rest of `_log_snapshots`.

### State — `resolved_alert_state.json` on the `data` branch

`{"high": "YYYY-MM-DD", "low": "YYYY-MM-DD"}` — the last day each variable was
alerted. One combined file (not per-variable) keeps the workflow changes minimal.
Persisted on the `data` branch alongside `cli_alert_state.json`.

### Workflow — `.github/workflows/log.yml`

- **Restore** step: add
  `git show origin/data:resolved_alert_state.json > resolved_alert_state.json 2>/dev/null || true`.
- **Publish** step: add the `cp` + `git add -f` lines for `resolved_alert_state.json`.
- `NTFY_TOPIC` env is already passed to the "Append this snapshot" step — no change.

## Data flow

The every-10-min Action builds `cli_snap` (today's high/low with `resolved`),
checks each variable's displayed Resolved %, and on the first run where a
variable is ≥70% (and not yet alerted today) sends one ntfy and records the day.
Later runs that day stay quiet for that variable; the other variable fires
independently when it crosses.

## Error handling

Best-effort throughout: a bad snapshot field, a send failure, or a corrupt state
file skips the alert (logged) and never affects the forecast/consensus logging
around it. An empty state file (from the `git show … || true` restore) is treated
as `{}`.

## Testing

- **Extraction**: `model.displayed_resolved` returns the same values market_view
  did — 100% window-closed → 100; convective/front day clamped to 90; and
  `market_view.displayed_resolved` still resolves (re-import) to the same function.
- **Threshold**: `_maybe_alert_resolved` fires at 70, not at 69.
- **Independence**: high ≥70 while low <70 → only the high alert sends.
- **Once-per-day + re-arm**: two runs same day → one send per variable; a run the
  next day sends again.
- **Empty state file**: a 0-byte `resolved_alert_state.json` doesn't block sends.
- (`notify.send_ntfy` mocked; `RESOLVED_STATE_PATH` monkeypatched to a tmp file;
  snapshots are small hand-built dicts.)

## Out of scope

- On-page indicator (push only).
- A combined "both ≥70%" alert (explicitly chose separate per-variable).
- Any threshold other than 70 (tunable via the constant, but only 70 ships).
- Alerting on the raw (unclamped) resolved value.

## Decisions locked in

- Separate per-variable alerts (not combined).
- Push only (no box).
- Threshold 70 on the **displayed** Resolved %.
- Message: title `Dallas {High,Low} locking in`, body `{pct}% resolved · ≈{consensus}°F`.
