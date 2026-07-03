# Betting-time edge measurement + settlement-gap logging (Plan C)

**Date:** 2026-07-03
**Status:** draft, pending review
**Area:** new `betting_log.py`, new `edge_report.py`; `scheduled_log.py` (extend),
`.github/workflows/log.yml` (carry a second log), `settlements.py` (reuse), tests.
No change to the live `model.py` forecast path.

## Problem

The goal is to restore trustworthy **3-3:30 pm CDT** bets on the KDFW high. Two
weeks of wins turned out to be *with-the-market* favorites (agreed, mid-bin days),
not the model beating the market — confirmed by the user and consistent with the
2026-06-30 benchmark (**market closer than the model 60%** of the time on highs,
model MAE = market MAE = 1.1). In the current hotter regime those easy days have
dried up, so the subjective confidence is gone. Before changing any model logic we
need to **measure** whether the model has genuine edge, and where.

Three findings block that measurement today:

1. **Nothing is logged at betting time.** `forecast_log.record` upserts on
   `(target_date, variable, lead_bucket, basis)` (forecast_log.py:27, :160), so the
   every-15-min scheduled run overwrites the same-day row all day — last write wins.
   The 10 same-day-high rows we have were captured at 23:00 (×5), 17:00 (×3), 22:00,
   12:00 — **zero at 15:30**. We cannot retro-measure the betting-window edge.

2. **The offset the user asked about is a flat guess in that window.**
   `settlement_offset.high = 0.89` (flat; the `_offset_bucket` two-bucket path is
   dormant). Before the solar-noon peak gate (~16:46 CDT in July) the CLI high is
   `hourly_consensus + 0.89`; only *after* the gate does it swap to the measured
   `observed_cont − observed` (model.py:465-471). So at 15:30 it does not respond to
   today's weather.

3. **We don't know if a dynamic offset would help.** Over the 10 days with both
   bases, the actual settled gap (CLI − hourly) is 0/0/0/0/1/1/1/1/2/2 (mean 0.80,
   sd 0.75). The flat 0.89 as a gap predictor scores **MAE 0.62 / RMSE 0.75**. A
   perfect dynamic offset removes ~0.6°F; the *realistic* recovery from anchoring on
   today's live continuous-minus-hourly gap is **estimated ~0.3-0.4°F** — but that
   is unmeasured, because we have no betting-time snapshots and no stored continuous
   history. The gap matters only on **boundary days** (hourly forecast within ~1°F
   of a Kalshi bin edge); on mid-bin days it changes no bins.

## Goal

Stand up a **betting-time capture → settlement join → edge report** pipeline that
answers, from data:

- **Q1 (edge):** at each betting-window slot, does the model beat the Kalshi market
  — overall, and on the subset where the two *disagree* on the bin?
- **Q2 (offset):** does today's **live continuous-minus-hourly gap** at 15:30
  predict the settled gap better than the flat **+0.89**, enough to flip boundary
  bins toward the settled value?

Measure-first: **no live model change** until the data clears an explicit decision
gate (below). Ship a one-shot retro on the existing 10 days as a weak prior.

## Non-goals / out of scope

- **Changing the live offset or any `model.py` forecast behavior.** That is a
  separate, follow-up spec, gated on this project's results.
- **Bet sizing or automation.** This measures; it does not place or size bets.
- **The Robinhood / hourly page** (`settle_offset is None`) — untouched.
- **The existing `forecast_log` upsert semantics** — untouched; we add a *separate*
  log so same-day snapshots are no longer clobbered.

## Design

### 1. Betting-time capture — `betting_log.py` + a scheduled entry

A **separate** persisted file `betting_log.jsonl` (published on the `data` branch
alongside `forecast_log.jsonl`), so it is not subject to the forecast_log upsert.
Keyed on `(target_date, variable, capture_slot)` where `capture_slot` is the local
wall-clock label of the snapshot — so each slot's row persists instead of being
overwritten.

**Capture slots (default):** `15:30, 16:00, 16:30, 17:00` CDT. This brackets the
betting window *and* the ~16:46 peak-lock transition, so we can measure how much the
lock actually buys (15:30/16:00 pre-lock vs 17:00 post-lock) rather than assume it.

**Row schema** — one per `(target_date, variable, slot)`:

| field | meaning |
| --- | --- |
| `target_date`, `variable`, `capture_slot`, `captured_at` | keys + exact stamp |
| `hourly_consensus`, `cli_consensus` | model center, both bases |
| `flat_offset` | the `+0.89` (or bucketed) offset actually used |
| `live_gap` | `observed_continuous − observed_so_far` at capture (the dynamic candidate) |
| `observed_so_far`, `observed_continuous` | realized-so-far, both feeds |
| `peak_locked`, `sigma_used` | lock state + spread at capture |
| `model_bins` | top-N bins + probabilities (CLI basis) |
| `market_ev`, `market_buckets` | Kalshi implied (best-effort; omitted on outage) |

The snapshot reuses `model.snapshot(calib, settle_offset=off, continuous_obs=True)`
and `kalshi.implied_block(...)` exactly as `scheduled_log.py` already does; the
market block is best-effort (an outage omits it, capture still writes).

**Firing:** extend the existing every-15-min run (external cron + `log.yml`
fallback). A slot guard writes to `betting_log` only when local time is within
**±7 min** of a slot (tolerating the scheduler's jitter without double-writing —
two fires in one slot upsert on the slot key). `log.yml` is extended to restore and
republish `betting_log.jsonl` from/to the `data` branch next to the existing log. No
new infrastructure.

### 2. Settlement join

`settlements.jsonl` already records both `cli` and `hourly` settled highs
(`actual_gap = cli_high − hourly_high`). The report joins each betting_log row to its
day's settlement by `target_date`. No new capture needed here — reuse
`settlements.load()` / the existing recorder.

### 3. Edge report — `edge_report.py`

Joins `betting_log` × `settlements` and, **per capture slot**, emits:

- **a. Bin accuracy:** model top-bin correct % vs market top-bin correct %.
- **b. Point error:** model MAE vs market MAE (°F, CLI EV vs settled CLI).
- **c. Disagreement subset** (model top-bin ≠ market top-bin): win-rate for each
  side — *this is the actual edge test.*
- **d. Offset analysis (Q2):** MAE/RMSE of `flat_offset` vs `live_gap` as predictors
  of `actual_gap`; and a bin-flip count — "swapping flat→live_gap would have moved
  the bin toward / away from the settled bin."
- **e. Slices:** boundary day (hourly consensus within **1.0°F** of an even|odd
  Kalshi bin edge) vs mid-bin; and coarse temp band.

Output to a dated, benchmark-style folder `docs/benchmarks/<date>/edge/` — CSVs plus
a short `ASSESSMENT.md`, matching the existing `docs/benchmarks/2026-06-30/`
conventions so snapshots diff cleanly over time.

### 4. One-shot retro (weak prior)

Run the report logic now on the 10 same-day + 10 day-ahead days in the live
`forecast_log` (fetched from the `data` branch). **Clearly labeled** n=10, mixed
evening capture times, whole-degree settlements → directional only, not a verdict.
Its only job is to catch a glaring anti-edge before we wait weeks.

### Data flow

```
every 15 min ──► betting_log.py ──(slot guard ±7min)──► betting_log.jsonl (data branch)
                     │  reuses model.snapshot + kalshi.implied_block
day settles  ──► settlements.jsonl (existing)
on demand    ──► edge_report.py: betting_log × settlements
                     └──► docs/benchmarks/<date>/edge/{*.csv, ASSESSMENT.md}
```

## Decision gate (when the measuring ends)

Target **~25 betting-time days** (~3-4 weeks, one usable settled day per day). Then a
follow-up spec ships a **dynamic offset** only if, at the **15:30** slot, both hold:

1. **Q2:** `live_gap` RMSE beats `flat_offset` RMSE by a margin beyond noise
   (≥ **0.15°F** and sign-consistent across the sample), **and**
2. **Q1:** on **boundary days**, the flat→live_gap swap moves the bin toward the
   settled bin more often than away, and the model's disagreement win-rate is
   **> 50%**.

If either fails, the flat offset stays and we record the **null result** in the
benchmark — a real outcome, not a failure.

## Open decisions (defaults chosen; adjust on review)

- **Capture slots** — `15:30/16:00/16:30/17:00 CDT`. Add earlier (15:00) or later?
- **Separate `betting_log.jsonl`** (recommended) vs extending the `forecast_log`
  schema with a slot key. Separate file is cleaner and can't regress the existing
  upsert; chosen unless you object.
- **Decision N** — 25 days. Raise for more power, lower to act sooner.
- **Boundary definition** — hourly consensus within **1.0°F** of an even|odd Kalshi
  bin edge.

## Risks

- **Small samples + whole-degree settlement quantization** → early reads are
  directional; the decision gate waits for ~25 days deliberately.
- **Morning/afternoon gap ≠ peak-moment gap** — the exact hypothesis under test; the
  report measures it rather than assuming it.
- **Scheduler unreliability** — GitHub's cron is loose; the external cron-job.org
  15-min trigger already carries the reliable cadence, and the ±7 min slot guard
  tolerates jitter. A fully missed slot just yields one fewer row.
- **Look-ahead** — the retro and report are *post-settlement analysis only*; no live
  forecast path consumes them, so no lookahead is introduced. A future offset change
  would reuse the existing `live`-gated measured-gap machinery.

## Testing (TDD)

**`betting_log` (unit):**
- Two writes in the same `(date, variable, slot)` upsert to one row; different slots
  both persist (the anti-clobber guarantee).
- `forecast_log.jsonl` is untouched by a betting_log write.
- Row schema complete; market block omitted (not crashing) when `implied_block`
  raises.
- Slot guard: a `now` inside ±7 min of a slot writes; outside does not.

**`edge_report` (unit, deterministic fixtures):**
- MAE/RMSE, disagreement win-rate, and boundary classification on a hand-built
  betting_log × settlements fixture match expected values.
- Flat-vs-live_gap comparison and the bin-flip toward/away count are correct on a
  fixture with known gaps.
- Retro runs on a fixture `forecast_log` (evening captures, n small) without error
  and labels itself directional.

**Unchanged (must stay green):** existing `forecast_log`, `scheduled_log`,
`settlements`, and `model.snapshot` tests — this project adds files and one guarded
branch to the scheduled entry; it changes no forecast math.

**Verification:** new tests red→green; full `pytest` green; one live dry-run of the
capture at a real slot writes a well-formed `betting_log` row; the retro produces a
benchmark folder.
