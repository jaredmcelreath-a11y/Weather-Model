# CLI daily-min anchor for the Kalshi low

**Date:** 2026-07-03
**Status:** approved, pending implementation
**Area:** `model.py` (`predict_variable`, `snapshot`/`_predict_from`), `sources/station_history.py` (reuse), `config.py`, tests

## Problem

On the Kalshi page (CLI settlement basis), today's **low** displays the wrong
bin. Verified live at **2026-07-03 07:15 CDT**:

- Model low: consensus **78.6**, bins **79 → 47.8%**, 78 → 41.2%, 77 → 10.3%
  (top bin = 79), `peak_locked=False`.
- Market / actual CLI settlement: **78**.

The gap is a basis-anchoring bug, not model noise. Three facts combine:

1. The daily low occurred ~05:00; at 07:15 the temp is barely off the trough
   (hourly `observed_so_far = 78.98`), so the low has **not locked**
   (`locked_ratio = 0`, sigma floored at 0.8).
2. The reported consensus **78.6** sits just above the **78.5** bin boundary, so
   the mode rounds *up* into the 79 bin even though 78 is nearly tied.
3. The consensus is anchored to the **hourly** obs (78.98). The continuous
   reading only reaches the hard bound, where the +0.9°F spike-haircut cancels it
   (`min(78.98, 78.8 + 0.9) = 78.98`). The nowcast/consensus never see a
   sub-hourly value.

### Why the two obvious fixes don't work

There are two continuous sources, and only one can resolve 78 vs 79:

| Source | Today's low | Granularity |
| --- | --- | --- |
| 5-min ASOS feed (current `observed_cont`) | **78.8°F** (= 26 °C) | whole-°C — cannot distinguish 78 from 79 |
| IEM daily-summary `daily.py` (`fetch_actual_cli`) | **78°F** | whole-°F — already correct at 07:15, = settlement |

Anchoring to the 5-min feed (78.8) still rounds to the 79 bin. Only the
daily-summary min (78) — the exact variable Kalshi settles on, and the same
source calibration already trusts — reaches 78 live.

## Goal

On the Kalshi/CLI page, anchor **today's low** to the daily-summary CLI min when
it is available and colder than the hourly reading, so the consensus and mode
track the value Kalshi actually settles on. Center the distribution — do not
zero the neighboring bin.

## Non-goals / out of scope

- The **high** (`variable == "high"`) — its running daily-summary max is
  genuinely incomplete until evening; left on its current path.
- The **Robinhood** page (`settle_offset is None`) — byte-for-byte unchanged.
- The lock rule (`_extreme_locked`), sigma logic, hard bound, and the bias
  corrections — all unchanged.
- Any use of this value in **backtest/replay** — explicitly gated out (lookahead;
  see below).

## Design

### The CLI realized-low value

Define, for today's low on the CLI basis, a single "CLI realized low so far"
`cli_low`, chosen in priority order:

1. **Daily-summary min** for `day` (`fetch_actual_cli(day, day)[day][1]`), if
   present — whole-°F, authoritative, the settlement variable.
2. Else the existing 5-min `observed_cont` (unchanged fallback).
3. Else `None`.

The daily-summary min is fetched **once per live snapshot**, only when needed
(CLI basis, low), threaded into the obs bundle alongside `obs_continuous`
(e.g. `obs_series["cli_daily"] = (max, min)`), wrapped in `try/except → None`,
and subject to the project's existing fetch timeout / circuit-breaker path. On
any failure the value is `None` and behavior falls back to today's.

### The anchor — `predict_variable`, low + CLI basis only

Today, `settle_shift` for the low is set by the existing measured-gap branch
(model.py:465–468, gated on `locked or high_peak_in` **and** `observed_cont is
not None`); a **not-locked** low never reaches it and falls through to the flat
−0.36 average offset, which under-shifts.

Split the handling by variable so the two paths don't interfere:

- **High:** the existing branch is unchanged — still keyed on `observed_cont`
  and `locked or high_peak_in`.
- **Low, CLI basis** (`settle_offset is not None and variable == "low"`): use
  `cli_low` (daily-summary min preferred, else `observed_cont`) as the continuous
  value, and decide whether to trust it as a *measured* gap:

```python
gap = cli_low - observed                       # cli_low is not None, observed is not None
trust = False
if locked:
    # settled extreme: the measured value wins, even at gap == 0. Preferring
    # cli_daily here means a lock later today does NOT revert the anchor back
    # toward the whole-C 5-min value (78.8 -> 79).
    trust = -MAX_CLI_GAP <= gap <= 0
elif live and cli_daily is not None:
    # not yet locked: only the authoritative daily-summary, and only if it
    # tightens the low downward.
    trust = -MAX_CLI_GAP <= gap < 0
if trust:
    settle_shift = gap                          # measured gap, not the average offset
    settle_gap_std = 0.0                         # measured -> no gap widening
```

- **Colder-than-hourly guard (`gap < 0`, not-locked path only):** the CLI low can
  only *tighten* the low downward; a stale-warm or equal daily-summary value
  (`gap >= 0`) is ignored and the flat average offset applies as today. The
  daily-min-so-far is monotonic and always a physically valid ceiling for the
  final low, so no time gate is needed. The **locked** path keeps `gap <= 0`
  (a settled low's measured gap of exactly 0 must still win over the average
  offset — this is today's behavior and must not regress).
- **Sanity clamp (`gap >= -MAX_CLI_GAP`, `MAX_CLI_GAP = 3.0°F`, both paths):**
  bounds the damage from a glitched daily-summary cold spike. The daily-summary
  is a QC'd product (unlike the raw 1-min feed), so this is belt-and-suspenders.
- **Backtest:** with `live=False` and no `cli_daily` in the obs bundle, the
  not-locked path never fires and `cli_low` degrades to `observed_cont` (also
  absent in replay) → the low uses the average offset exactly as today.

Sigma, `locked_ratio`, the hard bound (still `observed_bound`, unchanged), the
reported `observed_so_far` / `observed_continuous` display fields, and the high
are all untouched. A constant `settle_shift` leaves sigma and `locked_ratio`
unchanged (same invariant the existing offset relies on).

### `live` gating (backtest safety)

The rule fires only when `live is True`, mirroring `convective_sigma`. Backtest
and obs-replay call `predict_variable` with a today-relative `now` on past days;
the settled daily-summary min for a past day is the CLI *truth* the backtest
grades against, so anchoring to it live-in-replay would be lookahead. Gating on
`live` keeps backtest honest (it continues to use the average offset / no
`cli_daily` in the obs bundle).

### Config

Add `MAX_CLI_GAP = 3.0` (°F). No other constants change.

### Expected effect

**2026-07-03, 07:15 CDT:** `cli_low = 78`, `observed = 78.98`, `gap = -0.98`
(within `[-3, 0)`), so `settle_shift = -0.98`, `gap_std = 0`. Samples anchored at
~78.98 shift to center **78.0**; with the floored sigma ≈ 0.7 and the hard bound
unchanged, bins ≈ **78: ~52%**, 79: ~22%, 77: ~22%. Mode flips to 78, matching
the market; 79 retains honest residual probability.

**Behavior shift beyond today:** on any night the daily-summary min is colder
than the hourly reading (per calibration, the CLI low runs colder than hourly on
a meaningful minority of nights, occasionally by 1–2°F), the Kalshi low now
tracks the daily-summary min directly instead of the flat −0.36 offset. This is
the intended correction, but it is broader than the single reported case.

## Testing (TDD)

**`predict_variable` — CLI low anchor (unit / small integration):**

- **Anchors to daily-summary min:** obs bundle with `cli_daily` min colder than
  the hourly low (e.g. hourly 79, cli 78), `live=True`, CLI `settle_offset` set →
  `consensus` ≈ the cli min and the top bin == `round(cli_min)`. The same inputs
  without `cli_daily` (today's code) keep the top bin at the hourly bin — the
  behavior change.
- **Colder-than-hourly guard:** `cli_daily` min ≥ hourly obs → no shift from this
  rule; the flat average offset applies exactly as today.
- **Sanity clamp:** `cli_daily` min more than `MAX_CLI_GAP` below the hourly obs
  (e.g. hourly 79, cli 70) → ignored; falls back to today's behavior.
- **Fetch failure:** `cli_daily` absent / `None` → unchanged from today.
- **Live gate:** same colder `cli_daily` (not locked) with `live=False` → no
  shift (backtest safety).
- **5-min fallback preserved:** no `cli_daily` but `observed_cont` colder and
  locked → matches the existing locked measured-gap result.
- **Locked prefers daily-summary:** locked low with both `cli_daily` (78) and a
  warmer whole-°C `observed_cont` (78.8) → anchors on 78, not 78.8 (a later lock
  today must not revert toward the 5-min value).
- **Locked gap == 0 no regression:** locked low with `cli_low == observed` →
  measured gap 0 applies (no shift), the average −0.36 offset does **not** creep
  back in.

**Unchanged (must stay green):**

- **Robinhood:** `settle_offset is None` path — no `cli_daily` fetch, no shift.
- **High:** CLI high with a `cli_daily` max present → high branch ignores it
  (low-only rule).
- Existing `tests/test_cli_basis.py` locked/unlocked-low cases.

**Verification:** new tests red→green; full `pytest` green; spot-check a live
Kalshi snapshot on a colder-CLI morning shows the low consensus == the
daily-summary min and the top bin matching the market.
