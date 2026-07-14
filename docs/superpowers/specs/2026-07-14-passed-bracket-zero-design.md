# Zero out contract brackets the settled extreme has already passed

**Date:** 2026-07-14
**Status:** Approved — ready for implementation plan
**Basis affected:** Kalshi / CLI settlement basis (both high and low)

## Problem

On the Kalshi page, a temperature bracket the day's extreme has **already passed** can
still show a non-zero model probability. Observed live on 2026-07-14:

```
hourly obs_min   = 75.92
cont min corrob  = 75.2
cli_daily min    = 75.0      <- the authoritative NWS CLI daily-summary min (what Kalshi settles on)
mass >= 76       = 0.2385    <- the "76 or above" bucket
bin 76           = 0.239     (bins 77+ already zeroed; bin 76 survives)
```

The `76+` bucket is impossible — the settled low is 75 — yet the model prints ~24% on it.

### Root cause

`predict_variable` in `model.py` builds a per-bin distribution and then calls
`_apply_hard_bound(probs, variable, observed_bound)` (model.py:730), which zeros bins the
observed extreme has ruled out and renormalizes (the renormalize *is* the mass
reallocation we want).

For the **low**, `observed_bound` is floored on the hourly ASOS min and the
**≥2-corroborated** continuous min (`min(obs_min, corroborated_cont_min + 0.9)`), a
deliberate anti-blip guard (see `[[convective-low-humility]]`) so a lone cold 5-min
reading can't wrongly lock the low. In the reproduction that floor is **75.92**, so the
bound only zeros bins ≥77 and bin 76 keeps its mass.

But Kalshi does not settle on the ASOS min — it settles on the **NWS CLI daily-summary
min**, which the model already fetches (`obs["cli_daily"]`, model.py:621) and currently
uses only as a soft *anchor* for `settle_shift`, explicitly "never a settlement floor"
(model.py:806). That authoritative value already reads **75.0**, so 76+ is genuinely
impossible.

## Fix

Use the CLI daily-summary extreme — the exact Kalshi settlement variable — as a **hard
floor** for `_apply_hard_bound`, on the CLI basis only, when it tightens the bound toward
the realized extreme.

### Low

In the low + CLI-basis branch of `predict_variable` (after the existing `settle_shift`
logic, where `cli_daily` is already read):

```python
# The CLI daily-summary min IS the Kalshi settlement variable. When the authoritative
# whole-degF summary already reports a min colder than our bound floor, brackets the
# settled low has passed are impossible -- floor the hard bound on it. Live-only (no
# backtest lookahead), downward-only, MAX_CLI_GAP-capped. Does NOT touch the
# corroboration/anti-blip logic -- a lone cold 5-min reading still cannot lock the low.
if (live and cli_daily is not None and cli_daily[1] is not None
        and cli_daily[1] < observed_bound
        and observed - cli_daily[1] <= MAX_CLI_GAP):
    observed_bound = cli_daily[1]
```

### High (symmetric)

Mirror it for the high using the daily-summary **max** (`cli_daily[0]`), flooring the
bound *upward* when the summary is hotter than the current high bound floor:

```python
if (live and cli_daily is not None and cli_daily[0] is not None
        and cli_daily[0] > observed_bound
        and cli_daily[0] - observed <= MAX_CLI_GAP):
    observed_bound = cli_daily[0]
```

The high already trusts a lone spike (`min_support=1`, since Kalshi settles on the raw CLI
max), so it is usually covered; this closes the same gap when the summary catches a peak
the ASOS feed missed.

`cli_daily` is currently read only inside the low branch. Hoist
`cli_daily = obs_series.get("cli_daily", {}).get(day)` so both branches share it.

## Guards (invariants the fix must preserve)

- **CLI basis only.** `cli_daily` is populated only when `continuous_obs=True` (the Kalshi
  page). The Robinhood/hourly basis is untouched.
- **`live`-only.** Backtest/replay must never receive the settled value as lookahead — the
  fix is inert when `live=False`.
- **Tighten-only.** Low floors *down* (`cli < observed_bound`); high floors *up*
  (`cli > observed_bound`). Never loosens the bound.
- **`MAX_CLI_GAP=3.0°F` sanity.** Ignore a daily-summary value absurdly far from the hourly
  obs (`abs(observed - cli)` within the cap), so a corrupt summary can't zero live buckets.
- **Anti-blip untouched.** The corroboration/`observed_cont` logic is unchanged; a lone
  uncorroborated 5-min reading still cannot lock the low.
- **Fallback intact.** `_apply_hard_bound` still returns unbounded probs if the bound would
  zero everything (`total <= 0`, model.py:394).

## Whole-°F rounding correctness

The daily-summary value is whole °F. `cli_daily[1] = 75.0` means the settlement low is 75.
The bound test `t - 0.5 >= observed_bound` with `observed_bound = 75.0` zeros bin 76
(75.5 ≥ 75) and keeps bin 75 (74.5 ≥ 75 is false) — exactly correct. The high uses
`t + 0.5 <= observed_bound` symmetrically.

## Testing

1. **Low unit test** — construct obs with `cli_daily` min = 75, an uncorroborated hourly
   dip near 76, `live=True`; assert `sum(mass for bin >= 76) == 0` and `sum(all mass) ≈ 1`.
2. **High unit test** — `cli_daily` max = 92 hotter than the ASOS-derived floor; assert
   bins ≤ 91 are zeroed.
3. **Backtest-inert test** — same inputs with `live=False`; assert the bound is unchanged
   (fix does not fire).
4. **Sanity-cap test** — `cli_daily` min more than `MAX_CLI_GAP` below the hourly obs;
   assert the wild value is ignored (bound unchanged).

## Acceptance criteria

- On the reproduction data (cli_daily min 75, hourly 75.92), the `76+` bucket reads **0%**
  and its mass reallocates into the ≤75 bins; total still sums to 1.
- No change on the Robinhood/hourly basis.
- No change in backtest/replay results.
- Existing test suite stays green.
