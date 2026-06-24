# Design: Convective downside humility for the daily low

Date: 2026-06-23
Status: Approved (pending written-spec review)

## Goal

Stop the model printing false high confidence on a daily **low** when evening
convection could still set a new, lower minimum before midnight. On days with a
storm threat the model currently locks to the morning low and collapses its
spread to the observation floor, reporting ~90% on the standing bin while the
market correctly prices a real chance of a rain-cooled crash. This adds a single
gated **convective sigma floor**: when a storm signal is present, refuse to
collapse the low's spread, so the distribution stays honestly uncertain — and,
because the existing hard bound deletes everything above the observed low, the
widened spread lands entirely on the downside where a storm would take it.

Scope is deliberately minimal: it touches **today's low only, only when a
convective trigger fires.** Highs, tomorrow's low, and every storm-free day are
byte-for-byte unchanged.

## Motivation / empirical finding (KDFW, 2026-06-23)

A live worked example, which is exactly the failure this targets:

- Morning low settled at **79°F**. By mid-afternoon (93°F, far above 79),
  `_extreme_locked` returns True for the low: every source collapses to 79,
  `locked_ratio → 0`, and `sigma = max(sigma_base · locked_ratio, _SIGMA_FLOOR)`
  floors at **0.7°F** (model.py:406). Result: **~90% on the 78–79 bin.**
- Meanwhile severe storms fired on a stalled boundary **~60 mi NW** of the
  airport (active Severe Thunderstorm Warning for Wise/Jack/Parker/Palo
  Pinto/Young counties). The Kalshi implied-mean low fell 78.4 → 76.9 as traders
  priced the chance an outflow/MCS pushed a cold pool into DFW before midnight.
- **The model could not see any of it.** Its inputs are smooth gridded fields;
  no source carries point convective downside. Critically, point signals at the
  airport were weak — HRRR evening POP was **4%**, all models held low-to-mid
  80s — so the live danger lived **upstream** (boundary + warning), not in the
  point fields. A point-POP-only trigger would have under-fired today.
- The one existing "extra cooling" path (radiational cooling, model.py:367)
  fires only on **clear + calm** nights — the opposite of storm conditions — and
  is skipped once obs anchor the day. It cannot help here.

The outcome resolved in our favor (storms stayed west; market round-tripped to
~97%), but the model reported the same ~90% the entire time — at the 4 PM lock,
at the 27¢ panic, and at the 97¢ recovery. It cannot distinguish "lock" from
"coin flip." On the symmetric bad day — outflow reaches the airport before
midnight — it would still print ~90% on 78–79 and bet into a loss. The user
trades DFW lows regularly in convective season, so that day will come.

## Why a sigma floor is sufficient (no mixture needed)

`_apply_hard_bound` (model.py:236) already zeroes every low bin above the
observed low and renormalizes. So widening the low's sigma about its mode (the
standing 79) spreads mass both ways; the hard bound then deletes the upper half
and renormalization pushes all freed mass **below** 79. The one-sided downside
tail we want falls out of machinery that already exists — no synthetic samples,
no skew-normal, no second distribution builder. The feature reduces to: *on a
convective day, replace the locked sigma floor with a wider convective floor.*

There is precedent for widening sigma in place: the CLI settlement-gap term
already does `sigma = math.hypot(sigma, settle_gap_std)` (model.py:422).

## Architecture — one trigger, one gate

```
predict_variable(..., variable="low", today)
  build samples / locked_ratio / sigma   (unchanged)
  → if convective_risk(day, now) active:
        sigma = max(sigma, CONVECTIVE_SIGMA)   ← the only new line of effect
  → _bin_probabilities(...)                (unchanged)
  → _apply_hard_bound(...)                 (unchanged; makes the widening one-sided)
```

All new logic that *decides* whether to fire lives in a new isolated module;
`model.py` gains a few lines that read the decision and floor sigma.

## Component 1 — `convective.py` (new, pure-ish trigger module)

`convective_risk(day, now) -> bool` (v1 returns a boolean; a 0–1 severity is a
later enhancement that can scale the sigma). Active when **either** signal trips:

1. **Point signal** — POP and/or CAPE over the remaining window `[now, midnight]`
   at KDFW exceed thresholds. Sourced from a new
   `open_meteo_models.convective_window(day, now)` that fetches
   `precipitation_probability,cape` for the point — the same endpoint and parsing
   shape as the existing `night_conditions` (open_meteo_models.py:96), just a
   different variable list and a `[now, midnight]` window instead of `[0, 8)`.

2. **Upstream signal** — an active NWS **Severe Thunderstorm Warning** in the
   N/NW approach to KDFW. v1 uses a **curated fixed list of upstream NWS
   counties/zones** (the boundary-approach counties: Wise, Jack, Parker, Palo
   Pinto, Young, Denton, Cooke, Montague, …) queried via
   `api.weather.gov/alerts/active`. Storms here move SE toward the metroplex, and
   the airport sits on its north side, so this list is the cheap, deterministic
   proxy for "a cold pool could arrive before midnight." (Refinement, later: true
   radius/geometry distance from the alert polygon, and an upwind filter from
   storm motion.)

The module is best-effort: any fetch error returns `False` (no widening), so a
network problem degrades to today's behavior, never to a crash.

## Component 2 — `model.py` gate

In `predict_variable`, for `variable == "low"` and `day == today` (i.e. `now`
within the day), after `sigma` is computed and after the settlement-gap widening:

```python
if convective_risk(day, now):
    sigma = max(sigma, CONVECTIVE_SIGMA)
```

This intentionally floors *after* the `locked_ratio` collapse, so it overrides
the 0.7 floor precisely on the locked-low day this targets. It is purely a floor:
on a day whose forecast sigma is already wider than `CONVECTIVE_SIGMA` (early,
unlocked), it changes nothing. No change to consensus/mean — only spread — so the
reported point low is untouched; only confidence loosens.

## Component 3 — config knobs (config.py)

- `CONVECTIVE_SIGMA` — convective low spread floor, ~**2.5–3.0°F** (start 3.0).
- `CONVECTIVE_POP_MIN`, `CONVECTIVE_CAPE_MIN` — point-signal thresholds.
- `CONVECTIVE_UPSTREAM_ZONES` — the curated upstream county/zone list.
- Window is `[now, local midnight]`, reusing `local_day_bounds`.

## Trigger bias: intentionally lean toward firing

The trigger is binary and its two error modes are asymmetric:

- **False fire** (warning nearby that never reaches the airport): the low is a
  bit less confident than ideal — the *safe* direction. Costs a little edge on
  that day; can never make us overconfident, never touches the high.
- **Missed fire** (real storm day not caught): reverts to the false-90% bug.

So thresholds are tuned to fire a little too easily. The feature can only ever
*widen* today's low — never narrow it, never shift the mean, never affect the
high — so a false fire is strictly low-cost.

## Visibility

The accuracy / model panel gains a one-line indicator when the trigger is live
for today's low, e.g. `⚡ convective low risk active — low confidence widened
(σ≥3.0)`, naming which signal fired (point POP/CAPE vs upstream warning). Makes
the behavior observable rather than a silent spread change, mirroring the
"Active self-corrections" line from the self-correction layer.

## Error handling

- `convective.py` is best-effort: any exception or missing data ⇒ `False` ⇒ no
  widening. A new network dependency (the alerts feed) must never break a
  prediction.
- The model gate is a pure `max()` floor: with the trigger off, `sigma` is
  unchanged and the whole path is identical to today.
- Reuses the existing 600s disk cache (config.CACHE_TTL_SECONDS) for the new
  fetches so refreshes don't hammer the APIs.

## Testing

- **No-op guarantee:** trigger off ⇒ `predict_variable` output byte-identical to
  pre-change (dry/clear-calm day).
- **Today's pattern:** locked low at 79, trigger ON ⇒ 78–79 confidence drops
  from ~90% to materially lower, with real mass on 76–77 and below.
- **One-sidedness:** after widening + hard bound, **zero** probability above the
  observed low; mass only at/below it.
- **Floor semantics:** on an early unlocked low whose sigma already exceeds
  `CONVECTIVE_SIGMA`, the gate changes nothing.
- **High untouched:** identical high distribution with the trigger on or off.
- **Trigger unit tests:** point path (POP/CAPE over/under threshold) and upstream
  path (warning present/absent in the curated zones) each fire independently;
  fetch error ⇒ `False`.

## Scope / YAGNI

- Boolean trigger + sigma floor only. No severity-scaled sigma, no wet-bulb-aimed
  mixture, no calibrated P(crash) in v1 — all noted as later enhancements once
  storm-day data accrues.
- Upstream signal is a curated zone list, not geometry/radius math, in v1.
- No new persistence, scheduler, or writer. New reads reuse the existing cache.
- Today's low only. Tomorrow's low (already wide) and the high are out of scope.
