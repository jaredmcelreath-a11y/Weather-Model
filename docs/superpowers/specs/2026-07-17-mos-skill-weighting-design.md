# Skill-weight MOS/NBM for day-ahead accuracy

**Date:** 2026-07-17
**Status:** design (approved to spec-review)

## Problem

Day-ahead is the model's weakest surface. The 2026-07-13 deep dive measured
day-ahead **high 15% exact / 35% within-±1°F** with a **+0.9°F warm bias**. The
project's own diagnosis (`exact-bin-accuracy` memory) is that day-ahead is bounded
by **center accuracy (MAE)** — sigma already sits near its exact-bin optimum, so
the only real lever is getting the forecast center closer to truth.

The model already pulls the single best day-ahead product for an airport site —
**IEM MOS: LAMP (LAV) + NBM-short (NBS)** — statistical guidance calibrated to
KDFW's own history, purpose-built to beat raw NWP at 0–48h. But in the consensus
it is a second-class citizen:

- It is **not** one of the 7 skill-weighted systems (`ensemble_mean` + 5
  deterministic + `nws`). In `model._sample_weights`, `mos_*` labels fall through
  the `else` branch and are pinned to the neutral **`nws` weight** — no skill
  weight of their own (`model.py:299-300`).
- It folds in **bias-uncorrected** (group `guidance`, bias 0).

So the best day-ahead forecaster in the lineup is weighted like a neutral also-ran.
Giving MOS a real, data-driven skill weight is the most direct lever on day-ahead
center MAE.

## Goal / success criteria

- MOS/NBS becomes a first-class skill-weighted system, measured from a real
  day-ahead archive, gated by the existing walk-forward OOS safety net.
- **Primary metric:** day-ahead consensus MAE (high + low), walk-forward OOS,
  MOS-as-systems vs the current equal-systems baseline. Ship only if it wins (the
  gate enforces ≥0.02 MAE or falls back to uniform).
- **Secondary:** the +0.9°F day-ahead warm bias shrinks (NBM/LAMP are
  bias-calibrated to KDFW, so up-weighting them should de-warm the center).
- **No regression:** calm/degraded-MOS days stay byte-identical; if MOS doesn't
  beat baseline, the gate reverts to uniform system weights automatically.

## Non-goals (v1)

- **No MOS bias knob.** MOS group bias stays 0. NBM/LAMP are already
  bias-calibrated, so residual bias should be small. We *measure* MOS bias during
  validation and only add a knob later if it proves material. (YAGNI; avoids
  interaction with the settlement offset + `by_lead` correction.)
- **No per-lead system weighting.** The existing weighting is per-variable, applied
  at all leads; that stays. Per-lead weighting remains documented future work.
- **No fixed-lead NWP re-archive.** See the lead-fairness decision below.

## Approach (chosen: A + forward-log MOS)

Reuse the existing weighting machinery end-to-end; do not build a parallel path.
`_system_extremes` → `_system_weights` (inverse-MAE, shrunk to equal λ=0.25) →
`_weights_beat_equal` (walk-forward OOS gate). MOS enters as two new systems and
rides the same gate.

### Components

**1. `iem_mos.fetch_historical(start, end)` — archive backfill.**
For each past target day, fetch the **prior-day 12Z MOS run** (via the `runtime`
param the `mos.json` API already accepts) for LAV and NBS. Return
`{mos_lav/mos_nbs: (times, temps_f)}`, shaped like every other historical fetcher,
TTL-cached like `open_meteo_models.fetch_historical`.

- Verified 2026-07-17: the IEM archive retains 12Z runs ≥45 days back. NBS spans
  +72h, LAV +38h from a 12Z run — **both cover the next day's afternoon high and
  overnight low** at a genuine ~24–38h lead.
- A run that is missing or too short for a day/var is simply absent from the
  output; `_system_weights` already skips a system with no data on a day.

**2. Wire MOS into `_system_extremes`.**
Include `mos_lav` and `mos_nbs` as their own systems (two separate systems, not a
combined `mos`) in the `{day: {system: {high, low}}}` map, alongside the
deterministic models and `ensemble_mean`. Keeping them separate lets NBS (the
day-ahead workhorse) earn a higher weight than LAV (really a same-day product);
shrinkage handles their correlation.

**3. `_sample_weights` — the behavior change.**
Split the `else` branch: `mos_*` labels key to their own system weight;
`nws_*` keys to `nws`. This is the one line that actually lets MOS carry its skill
into the weighted consensus / bin mixture.

**4. Pass MOS systems to the gate.**
Add the MOS systems to the `systems` list handed to `_weights_beat_equal` and
`_system_weights` so the walk-forward gate scores the consensus *including*
MOS-weighted mass and can reject it if it doesn't help.

**5. Forward-log MOS (enabling half of approach B).**
Ensure the snapshot's `sources` dict carries MOS **per-model** extremes
(`mos_lav`, `mos_nbs`) so `forecast_log` records them at true live day-ahead lead.
No immediate behavior change; starts the honest live dataset that later refines
the archive's conservative weight.

### The lead-fairness decision

The Open-Meteo NWP archive returns a *short-lead* forecast per past day
(`calibration.py:13` scope note), while MOS is measured here at a true day-ahead
(~24–38h) lead. This mismatch biases **against** MOS (longer lead ⇒ more error ⇒
lower inverse-MAE weight). The direction is safe: it can only *under*-weight MOS,
never inflate it, and the walk-forward gate blocks any regression regardless. So:

- v1 ships a deliberately **conservative** MOS weight.
- The forward log (component 5), which captures every source at its true live
  day-ahead lead, is what later refines MOS to a fair apples-to-apples weight.
- We do **not** chase a fixed-lead NWP re-archive — the free tier can't pin one,
  and the gate makes it unnecessary.

## Data flow

```
prior-day 12Z run ──► iem_mos.fetch_historical ──►┐
open_meteo_models.fetch_historical (det) ─────────┤
open_meteo_ensemble.fetch_historical (ens) ───────┼─► _system_extremes
                                                   │     {day:{system:{high,low}}}
station_history actuals ───────────────────────────┘        │
                                                            ▼
                                          _system_weights (inv-MAE, λ=0.25)
                                                            │
                                          _weights_beat_equal (walk-fwd OOS gate)
                                                   pass │        │ fail
                                                        ▼        ▼
                                              skill weights   uniform systems
                                                        │
                              calibration.json weights ─┘
                                                        │
                                                        ▼
                        model._sample_weights  (mos_* → own weight; nws_* → nws)
                                                        │
                                    _collect_samples / _bin_probabilities / consensus
```

## Testing

- **Unit:** `iem_mos.fetch_historical` parsing (mock payload with a `runtime`);
  missing/short run → system absent, not a crash.
- **Unit:** `_sample_weights` routes `mos_*` to its own system weight and `nws_*`
  to `nws` (regression-guards the one-line change).
- **Unit:** `_system_weights` skips a MOS system absent on a day/var.
- **No-regression:** calm/degraded-MOS path unchanged (MOS absent ⇒ prior
  behavior); existing suite stays green.
- **Validation harness (measure-first deliverable):** walk-forward OOS day-ahead
  consensus MAE (high + low), MOS-as-systems vs equal-systems baseline; report the
  MAE delta, the gate's assigned NBS/LAV weights, and the change in day-ahead high
  bias. Also record the measured MOS bias (informs the deferred bias knob).

## Risks

- **IEM archive latency/gaps** on a 45-day backfill (many `runtime` calls). Mitigate
  with TTL caching + per-day skip; if a run is missing, that day's MOS system is
  simply absent.
- **Conservative under-weighting** from the lead mismatch (accepted; see above).
- **LAV correlation with NBS** diluting the weight split — shrinkage (λ=0.25) plus
  the OOS gate contain it; separate systems chosen deliberately.
