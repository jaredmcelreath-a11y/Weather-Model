# Front-aware locked low — design

**Date:** 2026-07-13
**Status:** approved design, pending implementation plan

## Problem

Once today's low locks (`_extreme_locked`: temp risen 2.0°F off the running min, or 0.8°F past sunrise — true by mid-morning every day), `_member_extreme` collapses every member to the observed minimum, discarding the forecast entirely. A **non-convective evening cold front** that undercuts the morning min before midnight therefore produces a confident, wrong forecast:

- The convective humility floor is POP-gated, so a dry front (POP ~5%) never arms it.
- The `lock_status` "Front Risk" warning compares `consensus < obs − 1`, which is unreachable post-lock (consensus ≈ observed once samples collapse).
- Kalshi settles on the full-day CLI min, not the morning low.

Rare in a Dallas summer (dawn lows), routine in autumn/winter (front-driven 23:59 minima). The June 26–28 storm-night misses (+3.7/+2.7/+3.6°F consensus errors) showed the failure shape: sigma widening alone doesn't move the recommended bin — the consensus error is what costs money.

## Decision summary

| Question | Decision |
|---|---|
| Response type | **Shift consensus + widen** — undercutting members pull their samples down; bins and sigma follow |
| Trigger gate | **Afternoon-only + margin** — anchored forecast temps at local hour ≥ 12, undercut ≥ 0.5°F below observed min |
| Scope | **Low only** — the high keeps its peak-postdates-trough guard (locks are asymmetric by design) |
| Validation | **Unit tests + historical replay** of 1–2 real KDFW front days |

## Mechanism

All model logic lives in `model.py` + `config.py`.

### `_member_extreme` (low, locked branch)

Today: `if locked and observed is not None: return observed`.

New behavior for `variable == "low"`:

```
undercut = min(anchored remaining forecast temps at local hour >= FRONT_SCAN_FROM_HOUR)
if undercut <= observed - FRONT_UNDERCUT_MARGIN:
    return undercut          # this member sees a front
return observed              # locked exactly as today
```

- "Anchored" = the existing `(obs_now − fc_now)` offset already applied to remaining hours, so a member's evening projection is corrected for its current error before comparison.
- Members with no post-noon remaining hours (e.g. `now` ≥ 23:00 with no forecast points left) fall back to `observed`.
- The high's locked branch is untouched. `_extreme_locked` itself is untouched — the lock still fires identically; only what a locked low member *reports* changes.

### New config constants

```python
FRONT_UNDERCUT_MARGIN = 0.5   # °F below the observed min a member's post-noon
                              # projection must reach to count as a front (clears
                              # anchor jitter; dawn wobble can't reach it)
FRONT_SCAN_FROM_HOUR = 12     # local hour the undercut scan starts — "a new low
                              # later today" is an afternoon/evening event; the
                              # 6–10am dawn-adjacent hours (the reason the early
                              # lock exists) are excluded
```

### Emergent behavior (nothing else computed)

- **Consensus** shifts down in proportion to the weighted member mass seeing the front (skill weights apply as everywhere else).
- **Sigma** reopens automatically: `locked_ratio = std(samples)/fullday_sd` grows when members disagree; a unanimous front collapses tightly *onto the front's level* instead.
- **Hard bound** still deletes bins above the observed min; undercut mass below survives.
- **Calm summer day:** every member's post-noon min sits above the morning min → all samples = `observed` → byte-identical behavior to today.

### `front_widened` output flag

`predict_variable` returns `front_widened: bool` — true when the low is locked and at least one member took the undercut path. Mirrors `convective_widened`.

## Dashboard changes (`market_view.py`)

1. **Resolved cap:** `displayed_resolved` caps at 90% when `front_widened` (reuse `CONVECTIVE_RESOLVED_CAP`; rename only if trivial). Without this, Resolved reads 100% all afternoon (the low's time window closes at 9am) while the model holds the low open — the same contradiction the convective cap fixed.
2. **Badge ordering:** in `lock_status`'s low section, move the front-risk warning check (`consensus < obs − 1.0`) **above** the `peak_locked` success branch, so a front day shows the amber "colder reading expected later" badge instead of a green "prime buy window." (With undercut members, consensus can now sit below observed while locked, making the check reachable.)
3. Optional caption (like the convective one) when `front_widened`: "Forecast front risk — models project a colder evening reading; the low may not be final."

## Interactions verified

- **CLI measured-gap anchor** (`settle_shift` from the daily-summary min on a locked low): a uniform shift applied to all samples, undercut members included. Unchanged.
- **Convective humility:** independent and stacking — it floors sigma, this moves samples; `sigma = max(sigma, conv_sigma)` semantics unchanged. A stormy front day can trigger both, which is correct.
- **Warm-low / cooling / lead-bias corrections:** pure-forecast-path only (`obs_now is None`), never coincide with a locked low. Untouched.
- **Backtest/replay:** the guard is purely forecast-driven (no live-only source), so it runs identically in backtest — no `live` gating (deliberately unlike convective).

## Testing

### Unit tests (`tests/test_front_guard.py` or extend `test_low_lock.py`)

1. Calm locked day — post-noon forecasts above the morning min → samples identical to current behavior, `front_widened` False.
2. Front day — members projecting evening temps 2°F under the min → those samples equal their undercut values; consensus drops; sigma widens; `front_widened` True.
3. Dawn-jitter immunity — a pre-noon dip below the min (hour < 12) cannot trigger.
4. Margin graze — an undercut of 0.3°F (< margin) is ignored.
5. Partial disagreement — 2 of 5 members undercut → consensus between observed and undercut, sample spread > 0.
6. No post-noon hours remaining — falls back to `observed`, no crash.
7. Badge — front day returns the warning badge, not the locked-success badge; calm day still returns success.
8. Resolved cap — `front_widened` caps displayed resolved at 90.

### Historical replay (validation script, not shipped code)

- Find 1–2 spring-2026 KDFW days where the CLI daily min occurred after 18:00 local (IEM 5-min archive), plus one calm control day.
- Replay `predict_variable` at several intraday `now` times (e.g. 10:00, 14:00, 18:00, 21:00) using archived Open-Meteo **deterministic** forecasts (the ensemble archive only retains ~5 days — accepted limitation; the mechanism is member-shape-agnostic) and IEM obs truncated to each `now`.
- Success: on the front days the guard reopens/shifts the low ahead of the front while today's code stays pinned to the morning min; on the control day the two are identical.
- Artifacts land in `docs/benchmarks/<date>/front-guard/`.

## Out of scope

- The high (protected by the peak-postdates-trough guard).
- `_LOW_WINDOW`/`covers_extreme` seasonal fixes and winter bin ranges (roadmap items #5).
- Robust/outlier-resistant self-correction stats (roadmap item #2).
