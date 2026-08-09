# A multi-model consensus temperature for all 20 Screen cities

Date: 2026-08-09

## Problem

Judging a Screen row means answering "where is this city's temperature actually
heading?", and today there are only two numbers to answer it with: `Ref`, which
is the NWS point forecast and nothing else, and the Kalshi price. When those two
disagree there is no third opinion — no way to tell whether NWS is the outlier
or the market is.

Dallas and Austin have had a five-model consensus since June. The other 18
cities have never had one, for no better reason than that nobody built it.

Add a **free, independent, per-city model consensus** shown beside `Ref`, and a
board covering all 20 cities so a number exists whether or not a bracket is
flagged there.

## Scope

**In:** the consensus number, its spread, a Screen-table column, a 20-city
board, and a forecast log for later scoring.

**Out, deliberately:**

- The consensus never gates a flag, never blends into `Ref`, never reaches
  `screen_alert`. Screening rules and the alert are byte-for-byte untouched, so
  the 66-settled `screen_score` track record stays comparable across the change.
- No scorer yet. Logging starts now; the report waits for data (see
  "Measurement" below).
- No per-city bias correction, skill weighting or sigma. See "Why equal weight".

## Cost, measured

One request returns every city and every model:

```
20 coordinates x 5 models x 72 hours  ->  200 OK, 0.7 s, 62 KB
```

Open-Meteo's published weighting is roughly one call per location (their cost
formula also scales with variables and forecast days, both of which sit inside
one unit here), so a pass should bill ~20 of the free tier's 10,000/day. At the
30-minute screen cadence that is ~960/day — under 10% of the allowance, with no
key and no payment. The response carries no rate headers, so this is an estimate
from the documented formula, not a measured figure; the first week's Action logs
will confirm it by the absence of 429s.

It runs inside GitHub Actions, on an un-throttled IP. The Streamlit Cloud
shared-IP 429 that forced the `det_models.json` fallback cannot affect this: the
app only ever reads the published document.

## Why equal weight, and not the KDFW pipeline

The consensus is the plain mean of five deterministic models — the same set
`config.DETERMINISTIC_MODELS` already trusts (GFS, ECMWF IFS, ICON, GEM, HRRR) —
with spread as max minus min. No bias correction, no skill weights.

Skill weights at KDFW came from months of self-scoring at *that station*.
Nothing equivalent exists for Denver or Miami, and inventing one would repeat
the 2026-07-17 season-readiness bug, where a number derived from no data printed
`0%` and produced a live `BUY NO +85`. Equal weight is the honest estimator when
you do not know which model is better *there*.

The log exists so this can change later on evidence rather than taste.

Rejected alternatives: the full KDFW pipeline per city (needs ~20x the
self-scoring history before its weights mean anything); the ensemble API
(~140 members gives a real distribution, but it is a heavier per-city endpoint
and a 5-point spread already answers "do the models agree?", which is all a
review screen needs).

## Architecture

Four pieces, each with one job.

### `sources/open_meteo_cities.py`

Given N coordinates, return the raw multi-location response. Knows the API and
nothing else.

Separate from `open_meteo_models.py`, which is bound to `config.station` and
`config.TIMEZONE` and is single-station by construction.

### `city_consensus.py`

Pure logic plus a `run` entry point. No Streamlit, no trading imports.

- fold an hourly series onto a city's LST climate day
- reduce each model to that day's extreme
- average across models, measure spread, count contributors
- build and write the published document

### `scan.yml`

A new step beside the existing screen step, on the same 30-minute
`screen-run` dispatch.

### `screen_view.py`

Reads the published document. Never calls Open-Meteo.

### Flow

```
dispatch
  -> screen.py run          (unchanged; publishes screen_reference.json)
  -> city_consensus.py run  (reads that reference for timezones + realized,
                             one Open-Meteo request, writes city_consensus.json,
                             appends the log on the hour)
  -> screen page reads city_consensus.json
```

### Two structural decisions

**A separate entry point, not a step inside `screen_pass`.** The fetch is one
request covering all cities; `screen_pass` is a per-city loop. Threading a batch
fetch through a loop not shaped for it is the wrong seam. Standalone also means
a broken consensus can never cost a screen pass, and the screen is the part that
already works.

**Timezones come from `screen_reference.json`**, published 60 seconds earlier in
the same job, rather than being resolved again. One definition of a city's LST
offset instead of two that can drift — the same mistake fixed on 2026-08-09
between the page's red highlight and the alert's scope. A missing reference logs
and skips, exactly as `screen_alert` does.

## The day fold

Open-Meteo will return `temperature_2m_max` directly. **Do not use it.** It
aggregates on local time *with* daylight saving, while the climate day Kalshi
settles on is a fixed-LST window. In summer that boundary is off by an hour —
the trap `climate_day_of_ticker` already documents for `close_time`.

So: fetch `hourly=temperature_2m` in `timeformat=unixtime`, and fold with
`screen_forecast.lst_offset_hours`, the same function the rest of the screen
uses.

**Known and deliberately uncorrected:** hourly-basis extremes read slightly cool
against the 5-minute CLI max Kalshi settles on — about +0.9 F at KDFW. Applying
that number to 19 other cities would be another invented constant. Log both and
let the per-city gap show up on its own.

## What is displayed

### Screen table: one new column

`Models`, immediately after `Ref`, reading `93.4 ±1.2` — consensus, then spread.
One cell rather than two: the table is already fourteen columns and the two
numbers are only meaningful together.

Adjacency to `Ref` is the point. `Ref 96.0 · Models 93.4 ±1.2` says NWS is three
degrees above a tight cluster, so the gap is measured from the outlier.
`Models 95.8 ±4.6` says nobody knows anything.

**`Models` is folded with realized temperature, like `Ref`.** `Ref` is the NWS
forecast folded with what has already happened (and on a `dead` row it is the
realized extreme alone). An unfolded consensus could show a high of 91 at 3pm
when 93 has already occurred — the nonsense `fold_realized` exists to prevent —
one cell away from a folded number, inviting a false comparison.

Folding needs the realized extreme at build time, so `screen.py` publishes the
per-city realized extreme it **already computes** into `screen_reference.json`,
and the consensus document stores both forms:

- `unfolded` — for the log and future scoring
- `folded` — for display

This is the same split `screen_reference.json` already uses for the NWS extreme.
Adding a key to that document is backward compatible; `screen_alert` ignores it.

No delta column here. The row already carries `Gap`, and a second delta beside
it would be two similar-looking numbers meaning different things.

### The 20-city board

Directly below the candidate table, above the track record — it answers "what
should I bet", not "how did I do".

```
City           Hi NWS   Hi Models    Δ      Lo NWS   Lo Models    Δ
Denver           95.0   92.1 ±1.4  −2.9       63.0   63.4 ±0.8  +0.4
Miami            91.0   91.2 ±0.6  +0.2       79.0   78.6 ±1.1  −0.4
```

Δ is consensus minus NWS, so a large value flags a city whose forecast — the one
every gap on the page is measured from — is contested. Today by default, with a
Today/Tomorrow toggle.

**The board is driven by the consensus document, not by candidate rows.** All 20
cities appear whether or not anything is flagged there, which is the reason the
board exists. Both `Hi NWS` and `Hi Models` are the **folded** forms, matching
the table's `Models` column and each other — an unfolded NWS beside a folded
consensus would make Δ meaningless by mid-afternoon. On the Tomorrow view
folding is a no-op, since nothing has been realized yet.

## Measurement

`city_consensus.jsonl` on `scan-data`, daily partitions through the existing
`scan_log.append_many`. One row per city / variable / day:

```json
{"ts":"2026-08-09T22:00:00Z","city":"DEN","day":"2026-08-09","variable":"high",
 "nws":95.0,"cons":92.1,"spread":1.4,"n":5,
 "models":{"gfs":92.0,"ecmwf":91.5,"icon":92.8,"gem":92.9,"hrrr":91.3}}
```

Unfolded values only, so a later scorer sees the forecast as a forecast rather
than contaminated by what had already happened.

**Hourly, not every pass.** 20 cities x 2 variables x 2 days is 80 rows; at
every pass that is 3,840 rows/day into a file `append_many` rewrites whole on
each append. Hourly is ~16 KB/hour and ~384 KB by day's end, and it is already
finer than the models update (6 h for the globals, 1 h for HRRR). The gate is
`now.minute < 30` in Python rather than a bash minute-test, so it is testable;
with dispatches at :00 and :30 it fires exactly once an hour.

**Per-model values are kept** — 60 bytes, and they are what later reveals that
ECMWF alone beats the mean in Denver. That finding is unrecoverable
retroactively.

**Truth needs no new pipeline.** `scan_settled.jsonl` already records Kalshi's
finalized outcome per bracket daily, and the winning bracket pins the settled
temperature to a 1-2 F range. This is most of the reason the scorer is deferred
rather than built now.

## Failure modes

| What breaks | What happens |
|---|---|
| Open-Meteo 429 or down | Document is not refreshed. Page shows the last one with its age; past `STALE_AFTER_HOURS = 6` the numbers read `—` with a caption saying why. |
| A model returns nulls | Dropped; consensus over the rest, `n` recorded. Routine for HRRR past 48 h, so tomorrow commonly runs `n=4`. Below `MIN_MODELS = 3`, no number. |
| Reference missing or has no timezone | Logs and skips that city, as `screen_alert` does. |
| Document unreadable | The board and column degrade to `—`; the candidate table renders regardless. |
| Anything at all | The screen pass and the alert are untouched — separate entry point, and `screen_alert` never reads this document. |

## Testing

Injected-`Deps` throughout, following `screen.py`. No network in tests.

- the LST fold across a summer climate-day boundary — the DST trap explicitly
- a model of all nulls dropped; the `n >= 3` floor returning no number
- consensus and spread arithmetic, including a single-model-short day
- the built document carrying both folded and unfolded forms
- staleness blanking at 6 hours
- the hourly log gate at `minute < 30`, and the day partition chosen
- view: the column sits after `Ref`, formats as `93.4 ±1.2`, falls back to `—`;
  board Δ sign and Today/Tomorrow toggle

Plus `scripts/check_city_consensus.py`: a live dry-run printing the built
document and writing nothing, mirroring `scripts/check_screen_alert.py`.
