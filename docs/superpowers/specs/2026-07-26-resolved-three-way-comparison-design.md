# Resolved three-way comparison (hybrid as the real number)

**Date:** 2026-07-26
**Status:** Approved design, pre-implementation

## Problem

The displayed "Resolved" metric changed over the past month. A month ago
(commit `89c23dd`, `market_view.py`) it was a pure ensemble-convergence
readout, `1 − locked_ratio`, which tracked the real approach to the day's
extreme but could climb-then-drop (a late peak breaking an early false lock
re-widens the spread). It was replaced (commit `6e98e9a`, 2026-07-09) with a
monotonic-by-construction blend, `1 − (1 − collapse)·(1 − tprog)`, where
`tprog` is a time-of-day clock term. The clock term inflates the number toward
~90% before the extreme has physically landed, which then required a stack of
display caps (convective, front-risk, dawn-low-forming) to stop it
over-reporting confidence.

The user preferred the original's feel — it tracked the actual convergence on
the peak. We want to introduce a convergence-driven "hybrid", make it the real
displayed number, and show the current and original numbers alongside it for a
few days so the winner can be chosen from logged data, not a glance.

## The three numbers

| Name | Formula | Notes |
|------|---------|-------|
| **Hybrid** (new, the real number) | `1 − (1 − collapse) · locked_ratio` | `collapse` = existing `resolved_collapse` (forecast mass the observed extreme has physically ruled out; 0 before obs). No clock term. Rises as the ensemble converges AND as obs rule out mass. Hits 100% when either completes. Pre-obs = 0 (locked_ratio 1.0, collapse 0). |
| **Current** | `1 − (1 − collapse)·(1 − tprog)` | Unchanged; the value already in `d["resolved"]`. |
| **Original** | `1 − locked_ratio` | The month-ago readout. Can be non-monotonic. |

Where `locked_ratio = std(samples_so_far) / std(full_day_samples)` (→0 as the
ensemble converges) and `collapse = resolved_collapse` (→1 as obs rule out mass).

## Decisions

1. **Hybrid = convergence-driven** `1 − (1 − collapse) · locked_ratio`.
   Becomes the headline "resolved number" for human-facing display.
2. **Display presentation:** three labeled mini-metrics side by side —
   `Hybrid | Current | Original` — per variable (High & Low), both cities.
3. **Review method:** log all three (raw) at every scheduled snapshot so the
   tracks can be reviewed/charted after a few days.
4. **Blast radius:** human-facing consumers switch to hybrid **now**; automated
   consumers stay on current until the hybrid is validated, then flip in one
   commit.

## Component changes

### 1. `model.py` — `variable()` return dict
Add two fields alongside the existing `resolved` and `resolved_collapse`:
- `resolved_orig` = `1 − locked_ratio`
- `resolved_hybrid` = `1 − (1 − resolved_collapse) · locked_ratio`

Both in the 0.0–1.0 range, rounded to 2 dp like the siblings. No other model
logic changes; `resolved` (current) and `resolved_collapse` are untouched.

### 2. `model.py` — `displayed_resolved(d, which="current")`
Parameterize the existing helper. `which`:
- `"current"` (default) → today's behavior exactly: reads `resolved`, applies
  the convective/front cap and the dawn-low-forming cap. **Default keeps every
  existing caller unchanged** (notably the 70% ntfy alert).
- `"hybrid"` → reads `resolved_hybrid`, applies the **same caps** as current
  (it is the live headline; stays safety-capped).
- `"original"` → reads `resolved_orig`, **no caps** (faithful to a month ago).

### 3. `market_view.py` — `render_variable()` card layout
- Row 1: `Consensus | Spread` (2 columns).
- Row 2 (new, temporary): three mini-metrics `Hybrid | Current | Original`,
  each via `displayed_resolved(d, which=...)`, reusing the keyed-container +
  mobile CSS pattern that holds a trio on one row. Handle the narrow-mobile
  wrap that Consensus/Spread/Resolved has hit before (compact labels; verify at
  390px per the verify skill).
- Clearly commented as a temporary comparison to be collapsed back to a single
  "Resolved" once a winner is chosen.

### 4. `market_view.py` — human-facing consumers → hybrid
- Lock badge: `lock_status` / the `locked_pct = displayed_resolved(d)` at
  ~line 1228 → `displayed_resolved(d, "hybrid")`.
- "% resolved" captions (~lines 1012, 1036, 1083) → hybrid displayed value.
- Verify `lock_status` itself: wherever it reads the resolved figure, route it
  to the hybrid so the badge and caption agree.

### 5. Untouched automated consumers (stay on current)
- `scheduled_log.py:174` — the 70% ntfy alert calls `displayed_resolved(d)`
  (default `which="current"`). No change.
- `trade_logic.py:31` — the auto-trader reads raw `d["resolved"]`. No change.

### 6. `scheduled_log.py` — snapshot logging
Ensure the persisted snapshot carries the **raw (uncapped)** fields:
`resolved_hybrid`, `resolved`, `resolved_orig`, `resolved_collapse`,
`locked_ratio`, and the flags `peak_locked`, `low_forming`,
`convective_widened`, `front_widened`. Because `model.variable()` now emits
`resolved_hybrid`/`resolved_orig` in `d`, confirm the snapshot writer persists
the full variable dict (or add the two keys to its field whitelist if it
selects fields). Raw + flags lets us reconstruct capped-vs-raw for any formula
at review time.

## Testing

- Existing `test_model_displayed_resolved`, `test_lock_status_convective`,
  `test_lock_status_front`, `test_low_forming_guard`, `test_resolved_alert`
  stay green (default `displayed_resolved` behavior unchanged).
- New:
  - `resolved_hybrid` math: pre-obs = 0; rises with convergence; → 1 when
    fully converged (`locked_ratio → 0`) OR fully ruled out (`collapse → 1`);
    a peak landing at the forecast mean (collapse ~0.5) still resolves via
    convergence, unlike `collapse` alone.
  - `resolved_orig` = `1 − locked_ratio`.
  - `displayed_resolved(d, "hybrid")` applies the convective/front/dawn-low
    caps; `displayed_resolved(d, "original")` does **not**.
  - Guards: the 70% alert path and `trade_logic` still read the **current**
    number (regression fence against an accidental global switch).

## Out of scope / follow-up

- Choosing the winner after a few days of logged data.
- Flipping the 70% alert + auto-trader to the hybrid (one commit, later).
- Collapsing Row 2 back to a single "Resolved" and restoring the
  Consensus/Spread/Resolved trio.
- A chart of the three tracks vs the realized extreme (can review from the log
  ad hoc; only build if a glance at the raw log proves insufficient — YAGNI).
