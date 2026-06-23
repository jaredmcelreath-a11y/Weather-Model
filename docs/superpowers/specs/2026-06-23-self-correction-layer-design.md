# Design: Gated self-correction layer (live log → model knobs)

Date: 2026-06-23
Status: Approved (pending written-spec review)

## Goal

Let the model improve itself over time from its own settled predictions, without
a human in the loop, while being structurally safe against overfitting on small
samples. Generalize the single existing feedback path (`sigma.by_lead`) into a
small, ordered set of **gated correction knobs** driven by the settled forecast
log. Ship the framework plus the highest-signal loop (lead-time bias) live now;
ship the remaining three loops dormant so they activate automatically as their
data matures.

This is a continuation of the accuracy subsystem: the forward log
(`forecast_log.jsonl`) and `scoring.py` already grade every shown prediction
against KDFW settlement. Today only one product of that scoring — empirical
per-lead sigma — feeds back into the forecast. This adds the missing loops.

## Motivation / empirical findings (settled log, KDFW, as of 2026-06-23)

- The forward log holds 44 records across 9 dates (2026-06-16 → 06-24), 32 hourly
  + 12 CLI. `scoring.score()` runs cleanly on both bases.
- **Day-ahead consensus is measurably warm and uncorrected.** Per-lead scoring:
  - lead 0 (same-day): high exact-bin 67%, low 67%; bias high −0.63, low +0.15.
  - lead 24 (day-ahead): high exact-bin **0%** (bias **+1.32**, σ 2.8), low 20%
    (bias **+1.82**, σ 1.82).
  - Nothing in the current pipeline corrects this residual lead-time bias.
- **The existing feedback loop is dormant, as designed.** `calibration._compute`
  calls `scoring.per_lead_sigma()` (gated at `MIN_LEAD_DAYS = 10`); with only 5–6
  settled day-ahead days it returns `{}`, so `sigma.by_lead` is absent from
  `calibration.json`. Expected, not a bug — and the template the new loops follow.
- **Per-source errors are already logged.** Each record carries `sources`
  (group-level predicted extremes) and, on CLI rows, `market`. So weight-tuning
  and market-blend loops have their inputs flowing already; `market_accuracy()` is
  currently n=0 (CLI market rows haven't settled yet).

## Local vs. Streamlit (context the design must respect)

- **Local:** `app.py` writes the log on each render (on-disk file); scoring reads
  the on-disk file. Log only grows while the dashboard is open. `sync_log.py`
  pulls the cloud history down.
- **Streamlit Cloud:** the dashboard does **not** write (`forecast_log.record`
  no-ops when `FORECAST_LOG_GH_*` is set). The scheduled GitHub Action
  (`scheduled_log.py`) is the sole writer to the `data` branch; the cloud
  dashboard reads the log back from that branch.
- **Implication:** all new feedback is computed inside `calibration._compute`
  (the daily recalibration), which already runs in both environments and reads the
  same log abstraction. No new writer or scheduler is introduced. The corrections
  persist via `calibration.json` exactly like the existing knobs.

## Architecture — one machine, four sockets

A **self-correction layer** inside the existing daily recalibration reads the
settled log (through `scoring`) and emits correction knobs into
`calibration.json`. `model.py` applies them in a fixed order so corrections never
double-count. Each knob is independently gated; a dormant knob is a strict no-op.

`model.py` application order (per variable, per lead bucket where applicable):

```
raw per-model forecasts
  → existing per-source bias correction        (unchanged)
  → [Loop 2] live group re-weighting           → consensus     (dormant)
  → [Loop 1] lead-time residual de-bias         → shifted consensus
  → [Loop 4] market blend                       → final point   (dormant)
  → per-lead sigma (existing) → [Loop 3] reliability sharpen → probabilities (dormant)
```

Rationale for order: Loops 2/1/4 all move the *mean* and must be sequenced so a
single miss is not corrected three times — re-weight the inputs first, then
remove the residual bias that remains after re-weighting, then blend the market.
Loops "sigma" and 3 both set *spread* and are applied last.

## Loop 1 (BUILD LIVE) — lead-time residual bias correction

**Measurement.** `scoring.score()` already produces
`by_lead[bucket][var] = {n, bias, sigma, ...}` where `bias = mean(consensus −
actual)` (**positive ⇒ forecast too warm**). Because scoring grades the
already-bias-corrected consensus that was actually shown, this is a *residual*
bias that sits on top of the existing per-source `bias` block — different
pipeline stage, no conflict.

**New function** `scoring.per_lead_bias(min_days=MIN_LEAD_DAYS, today=None)`,
sibling of `per_lead_sigma()`. Returns `{bucket: {var: correction}}` only for
buckets that clear the guards below; omitted buckets ⇒ no correction (model falls
back to no shift).

**Two guards (no fragile OOS split on ~6 points):**

1. **Sample gate:** include a `(bucket, var)` only when `n ≥ MIN_LEAD_DAYS`
   (reuse the existing 10).
2. **Shrinkage + significance:** emit `correction = bias × n / (n + K)`
   (shrinkage constant `K`, default chosen so a 10-day bias is damped ~⅓–½ and a
   30-day bias is nearly full), and only when `|bias| > Z · sigma/√n` (bias is
   statistically distinguishable from zero; default Z ≈ 1). A noisy bias gets
   shrunk toward zero; a persistent real bias survives and grows with data.

**Calibration output.** `calibration.json` gains
`bias_correction: {by_lead: {bucket: {var: correction}}}`, written next to the
existing `sigma.by_lead`, behind the same lazy `import scoring` already in
`_compute`.

**Application in `model.py`.** After the consensus/samples are built and before
per-lead sigma is applied, subtract `correction` for the active `(bucket, var)`
from the consensus and the sample set (so both the point forecast and the
probability mass shift together). Absent knob ⇒ unchanged behavior.

## Loops 2–4 (BUILD DORMANT) — activate on data

Each ships wired, tested, and gated to return empty until its data matures, in
the same spirit as today's dormant `sigma.by_lead`.

- **Loop 2 — live group re-weighting.** From the per-row `sources` group means,
  compute trailing per-group (deterministic / ensemble / nws) settled error and
  re-balance weight *across* groups, leaving the within-group backtest weights
  untouched. Gated on settled-day count; per-variable (high/low) like the existing
  weighting, since re-weighting can hurt the high. Emits to a `weights` overlay in
  `calibration.json`; dormant until the gate clears.
- **Loop 3 — reliability sharpening.** From `by_variable[var].reliability`, derive
  a multiplier on sigma that nudges stated confidence toward realized hit rate.
  Emits `sharpen` factor; dormant until enough settled days for a stable curve.
- **Loop 4 — market blend.** From `scoring.market_accuracy()` (model-MAE vs
  market-MAE on CLI), derive a blend weight pulling the consensus toward the
  market's implied forecast. Dormant while `market_accuracy` is n=0.

## Visibility

The "📊 Model accuracy" expander (`market_view._render_accuracy`) gains a compact
**"Active self-corrections"** line listing which knobs are currently live and
their values (e.g. `lead-24 high −1.1°F`, `low −1.5°F`; others: dormant, N more
days). Makes "learns on its own" observable rather than silent.

## Error handling

- All new feedback computation sits behind the existing best-effort `try/except`
  in `_compute` (a scoring failure must never break recalibration; the knob is
  simply omitted that day).
- `model.py` treats every missing/empty knob as a no-op, so a partial or absent
  `calibration.json` degrades to today's behavior.
- No new network calls, writer, or scheduler — reuses the daily recalibration and
  the existing log abstraction, so local and cloud behave identically.

## Testing

- **Loop 1 math:** shrinkage formula; significance gate boundary; sign
  correctness (a logged warm bias produces a *downward* consensus shift).
- **Gate transitions:** below `MIN_LEAD_DAYS` ⇒ knob absent (no-op); at/above ⇒
  knob present and applied. Same for the dormant loops' gates.
- **No-op guarantee:** with an empty/missing knob, `model.snapshot` output is
  byte-identical to pre-change behavior.
- **OOS replay:** using the existing obs-replay/backtest harness, confirm Loop 1
  does not worsen the held-out tail before it is allowed to activate.
- **Both bases:** scoring/feedback paths exercised for `hourly` and `cli`.

## Scope / YAGNI

- Build the framework + Loop 1 only. Loops 2–4 ship as gated-dormant stubs, not
  fully tuned, so the machine is complete but only the proven correction is live.
- No new persistence, no new scheduled job, no UI beyond the one status line.
- Reuse `MIN_LEAD_DAYS`, the lazy `import scoring`, and the `calibration.json`
  cache that already exist.
