# Instant alert on a new same-day Screen row

Date: 2026-08-07

## Problem

The Screen page lists brackets worth two minutes of attention, but only someone
watching the page sees them. The alerts that do reach the phone are about the
Dallas/Austin model — a different job — and the most useful signal, "a new row
just appeared for a bracket settling today", is silent.

Replace almost all of the current pushes with one: **a new same-day row on the
Screen table pushes to the phone, with as little delay as this architecture
allows.** Brackets closing tomorrow are explicitly not interesting.

## What is removed

Four pushes go away entirely, with their state files, tests and trigger code:

| Alert | Lives in |
|---|---|
| CLI Climate Report | `scheduled_log._maybe_alert_cli` |
| Resolved ≥ 80% | `scheduled_log._maybe_alert_resolved` |
| Storm Watch Active | `alerts.maybe_fire_events` (storm branch) |
| Front Risk | `alerts.maybe_fire_events` (front branch) |

**Morning Recap stays**, so `alerts.py` keeps `recap_body`, `_build_recap_body`,
its state file and `maybe_fire_events` — minus the two branches and the
`storm_body`/`front_body` builders.

The trader's own pushes are untouched: a separate loop, currently disabled.

## Why this cannot ride on the existing screen pass

The screen fires every 30 minutes (`log.yml` dispatches `screen-run` when
`minute % 30 < 10`). Alerting from it would be *worse* than the resolved alert
this is meant to replace. It also cannot simply be run more often: a pass is
~121 requests and ~70 s, and `scan_log.append_many` rewrites the entire
candidate log through the contents API every time, so its cost is quadratic.

The observation that makes a fast loop cheap: **a new row almost always appears
because a Kalshi price moved into the band.** Prices are cheap to poll. The NWS
*forecast* half of the reference is the expensive half and only refreshes
hourly. So poll prices often, and reuse the forecast the 30-minute pass already
computed.

## Design

### 1. `screen_reference.json`, published by `screen.py`

Every 30-minute pass writes one small JSON file to the `scan-data` branch
alongside the candidate log. It holds exactly what the fast loop cannot afford
to recompute:

```json
{
  "generated": "2026-08-07T18:30:00Z",
  "cities": {
    "KXHIGHDEN": {
      "station": "KDEN",
      "timezone": "America/Denver",
      "days": {"2026-08-07": 94.0}
    }
  }
}
```

The day value is the **unfolded** forecast extreme — `daily_extremes`' output
before `fold_realized`, because the alerter re-folds it against its own fresher
observations. The station id and timezone are carried so the fast loop never
pays for `points` and `observationStations` resolution (40 NWS calls it would
otherwise repeat from a cold runner every check). The variable is not stored;
it is derived from the series with `scan_log.variable_of_series`.

`screen.py` gains only this write. Its candidate log, rules and thresholds are
untouched, so `screen_score`'s measurement record stays comparable.

### 2. `screen_alert.py` — the fast loop

One check does:

1. **Read the reference** (1 GET) and **the alert state** (1 GET).
2. For each series in the reference, **poll Kalshi once** (`list_series_markets`,
   ~40 calls) and keep only markets whose `climate_day_of_ticker` equals the
   climate day currently in progress for that city's timezone. Tomorrow's
   brackets are dropped here.
3. **Fetch that city's observations** for the in-progress day (~20 NWS calls)
   and compute `realized`.
4. **Apply the existing rules unchanged.** `screen_rules.dead_candidate` first,
   against `realized_extreme(realized, variable)`; if it does not fire, and the
   reference is fresh, `screen_rules.forecast_candidate` against
   `fold_realized(reference_extreme, realized, variable)`. `dead` wins when both
   would fire — it is the half that claims certainty.
5. **Narrow to what the page would show:** the live NO ask must sit within
   `MIN_LIVE_NO_PRICE` (0.20) and `MAX_LIVE_NO_PRICE` (0.90).
6. **Diff against state, push one notification, save state.**

Roughly 61 requests and ~40 s per check. **Nothing is written to
`scan_candidates.jsonl`** — the alerter is read-only against it.

Two simplifications fall out of doing the fetch itself:

- **No stale-price problem.** `screen_view` has to fetch live prices separately
  because the logged firing price is up to hours old; here the candidate is
  built from the very market whose price is being tested, so the firing price
  *is* the live price and `no_ask_of` reads the same payload.
- **`dead` needs no reference at all.** It is a function of observations and the
  bracket alone, so it keeps working when the reference is missing or stale.

`MIN_LIVE_NO_PRICE` and `MAX_LIVE_NO_PRICE` move from `screen_view` to
`screen_rules`, with `screen_view` importing them. Two consumers of the same
band must not be able to drift apart — and `screen_alert` must not import
`screen_view`, which imports Streamlit and cannot run in a cron.

### 3. Staleness guard

If `generated` is more than **90 minutes** old (three missed 30-minute passes),
forecast rows are skipped for that check and only `dead` rows can alert. A gap
measured from a stale Ref is the stale-Ref failure the Drift
column exists to expose; pushing it to a phone as news is worse. The skip is
logged with the reference's age so a stalled screen is visible rather than
silently halving the alert.

### 4. Cadence: 5 minutes, with nothing to set up

`log.yml`'s external cron is the one reliable clock in this repo (measured
2026-08-04: 100 runs, median gap 10.0 min, max 10.1). A dedicated 5-minute
cron-job.org job would be cleaner in principle, but the 30-minute screen has
been waiting on exactly that manual step since 2026-08-04 and it was never
created; a design that needs nothing is worth more here than a tidier one.

So `log.yml` dispatches a `screen-alert` `repository_dispatch` on **every** one
of its own dispatch runs — the same fire-and-forget `curl` step as the existing
screen tick, with no `minute % 30` gate — and the new workflow performs **two
checks 5 minutes apart in one job**: check, `sleep 300`, check. The effective
cadence is 5 minutes off a 10-minute clock.

Runner time is free (the repo is public). `concurrency` uses the workflow's own
group with `cancel-in-progress: false`, so a late dispatch cannot kill a job
mid-sleep.

`scan.yml`'s hourly schedule remains the fallback for the reference file. There
is deliberately **no** in-repo `cron:` schedule for the alert workflow: GitHub
drops high-frequency schedules first (measured 62% delivery at hourly), so one
would fire unpredictably and add load without adding reliability.

### 5. State and dedupe

`screen_alert_state.json` on the `scan-data` branch:

```json
{"2026-08-07": ["KXHIGHDEN-26AUG07-B92.5", "..."]}
```

A ticker pushes **once**, the first time it qualifies. Days older than two are
pruned on write. The file is read every check but **written only when something
fires**, so a quiet check costs no commit — and most checks are quiet.

A ticker that qualifies, stops qualifying, then qualifies again does not push
twice. Re-alerting on re-entry would turn one bracket oscillating around the
20% floor into a stream of notifications.

### 6. The push

One notification per check, however many rows it found. Title `3 new screen
rows`; body one line per bracket:

```
Denver low 72+ · NO 35% · Ref 61 (11° gap)
Miami high 91-92 · NO 22% · DEAD (max 94 already)
```

City name from `scan_cities.city_name(series)`, bracket from the row's `label`.
Body is capped at 10 lines with a `…and N more` tail, so a pathological check
cannot produce an unreadable notification.

## Testing

Unit tests, all against fixtures with no network:

- day filter: a tomorrow-closing ticker never alerts; an in-progress one does,
  including across the LST/local-midnight boundary
  (the climate day ends 01:00 local during DST, not midnight).
- `dead` beats `forecast` when both fire for one ticker.
- price band: rows at 0.19 and 0.91 are dropped, 0.20 and 0.90 kept.
- staleness guard: with a 2-hour-old reference, a would-be forecast row is
  silent while a dead row still fires.
- dedupe: a ticker alerts once, stays quiet on the next check, and does not
  re-alert after dropping out and returning.
- state: quiet checks perform no write; the pruner keeps two days.
- message builder: multi-row body, the 10-line cap, and the singular/plural
  title.
- the removals: deleted tests for the four alerts; `alerts.maybe_fire_events`
  keeps its recap tests and gains one asserting storm and front data no longer
  push.

Live check: `scripts/check_screen_alert.py` runs **one real check with the push
stubbed**, printing what it would have sent and the reference's age. Run it
before the workflow is enabled. Both the screen and the price scanner shipped
with defects that only a live pass caught; this is the same discipline.

## Out of scope

- Alerting on brackets that close the next day.
- Any change to the screen's thresholds, the candidate log, or `screen_score`.
- A dedicated external cron job (documented in DEPLOY.md as the optional
  decoupling, not built).
- Re-alerting when a row's price moves after its first push.
