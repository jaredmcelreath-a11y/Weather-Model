# Corroboration-scaled high haircut

**Date:** 2026-07-20
**Status:** Design approved, pending backtest

## Problem

The live model's high-derivation path applies a **flat 0.9°F haircut** to the
sub-hourly continuous (5-minute) observed maximum before using it as the hard
bound:

```python
# model.py, high branch
cand = c_max - 0.9
```

The haircut exists because the NWS 5-minute feed reports whole °C. A reading of
"38°C" means the true temperature is anywhere in `[99.5, 101.3]°F` (the °C
bucket, midpoint 100.4°F). The flat 0.9°F shave assumes the **bottom** of that
bucket — the worst case — to protect against a lone sensor over-read that would
falsely latch the high.

That worst-case assumption is correct for a **lone** reading. It is
over-conservative when the °C level is **well corroborated**. Observed live on
2026-07-20 (KDFW): the 5-minute feed printed 38°C (100.4°F) on **4 separate
readings**, yet the model held its high at 99.7 — *below an already-realized
temperature* — purely because of the flat haircut. Kalshi implied 101.4. On
Kalshi's 5-minute-average CLI settlement basis, a sustained 38°C plateau settles
right around 100.0–100.4, not 99.5, so the model was the low party, not Kalshi.

See memory: `high-spike-latch-lag`, `kalshi-cli-settlement-basis`.

## Change

Replace the flat constant with a haircut that shrinks as corroboration grows.
Let `n` = the number of continuous 5-minute readings within `tol` (0.7°F, one
sub-°C step, the existing corroboration tolerance) of the trusted extreme
`c_max`:

```
haircut(n) = 0.90                            if n == 1     # lone → unchanged, glitch guard
           = max(floor, 0.90 - k * (n - 1))  if n >= 2
```

- `n == 1` (a lone reading, i.e. a glitch by definition) keeps the **full
  0.90°F** cushion. Glitch protection is therefore preserved *by construction* —
  the change is a no-op on any lone spike.
- As `n` grows, the cushion ramps down toward `floor` (expected ~0.2–0.4°F, i.e.
  sub-°C rounding noise), reflecting that a corroborated plateau genuinely
  reached that °C level.

`floor` and `k` are new constants in `config.py`, tuned by the backtest below.

### Scope boundary

`_trusted_high_max` is **unchanged**. It still decides *which* value to trust —
a lone raw spike (accepted only when forecast-supported per
`SPIKE_FORECAST_MIN`) versus the ≥2-corroborated peak. The new logic only
refines the *cushion applied to whatever value that function already chose*.
Clean separation: "which extreme" (unchanged) vs. "how much to shave it"
(new, corroboration-scaled).

`n` is derived by counting readings near `c_max` in the same continuous series
that `observed_so_far_robust` already scans — no new data plumbing. `n` counts
across the whole climate day at the peak's °C level (matching the existing
corroboration definition), not a contiguous recent window.

The low branch is untouched — it keeps corroboration and its own logic; the
haircut/spike-trust asymmetry between high and low is intentional (see
`peak-lock-diurnal-gotcha`).

## Backtest

A standalone script (not wired into the live pipeline) that validates the change
out-of-sample before any ship decision.

**Replay.** For each of the ~30 days in `settlements.jsonl`:

1. Reconstruct the day's 5-minute feed from IEM. `sources/nws_observations.py`
   already has `_iem_fallback` speaking the IEM ASOS METAR endpoint; extend/reuse
   it to fetch arbitrary past climate days (NWS only serves ~7 days back; IEM
   archives years). Reuse the existing whole-°C → °F decode.
2. Compute the trusted high via the real `_trusted_high_max` path under **both**
   the flat-0.9 haircut and the scaled haircut.
3. Score each result against the **actual settled CLI max** for that day (the
   Kalshi settlement variable, from `settlements.jsonl`).

**Report.**

- MAE of trusted high vs. actual CLI max: flat vs. scaled.
- Count of days changed by the new haircut.
- Regressions: days where scaled moved *away* from the truth (must be ~zero).
- Distribution of `n` on the days that changed (are the wins concentrated on
  solid plateaus, as expected?).

**Glitch-protection proof.** Separately, inject a synthetic lone +1°C reading
into N clean days and assert `scaled == flat` on those days (the `n == 1` path).
Proof-by-construction, but asserted in code so a future param change cannot
silently break the guard.

**Tuning + ship gate.** Grid-search `(k, floor)` over sane ranges
(e.g. `k ∈ [0.1, 0.3]`, `floor ∈ [0.2, 0.5]`); pick the lowest-MAE pair subject
to **zero glitch regression**. Report a sensitivity band since N≈30 is small.

**Do not auto-ship.** Like the `high-spike-latch-lag` precedent (a prototype that
backtested as DON'T-SHIP), this returns to the user with numbers. Merge only if
the OOS MAE improvement is real and robust across the sensitivity band.

## Files

- `model.py` — replace the flat `cand = c_max - 0.9` with a `haircut(n)` helper;
  count `n` from the continuous series near `c_max`.
- `config.py` — new `HIGH_HAIRCUT_FULL` (0.9), `HIGH_HAIRCUT_FLOOR`,
  `HIGH_HAIRCUT_K` constants.
- `sources/nws_observations.py` — extend the IEM path to fetch an arbitrary past
  climate day (backtest input).
- New backtest script (e.g. `backtests/haircut_scaling.py` or a `scripts/` file,
  following repo convention) — replay + tuning + glitch-injection assertions.
- Tests — unit tests for `haircut(n)` (n=1 → 0.9, monotonic decrease, floored),
  and the glitch-injection assertion.

## Success criteria

- Unit tests pass: `haircut(1) == 0.9`, monotonic non-increasing in `n`, never
  below `floor`.
- Backtest shows scaled MAE ≤ flat MAE with zero glitch regressions.
- On the 2026-07-20 live case (4× 38°C), the trusted high lifts from ~99.5 toward
  ~100.1–100.4, matching the realized plateau.
- No change on any lone-spike day.
