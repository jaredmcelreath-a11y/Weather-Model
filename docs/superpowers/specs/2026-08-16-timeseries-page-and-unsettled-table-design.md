# Timeseries page, unsettled-markets table, and the Strategy rename

Date: 2026-08-16

## Problem

Three unrelated frictions, grouped because they are all navigation-and-visibility
work and none of them changes a screening rule.

**1. The 5-minute observations live on someone else's site.** The reading that
decides a settlement is the running climate-day extreme, and the only place to
watch it accumulate is `weather.gov/wrh/timeseries?site=KXXX` — one page per
city, found by hand, unthemed, and outside this app. Twenty cities means twenty
bookmarks.

**2. There is no list of which markets are still live.** The Strategy page shows
brackets the screen flagged. It says nothing about the far more basic question of
where today's action even is — which cities have a market that has not already
collapsed onto one bracket. Finding that today means opening Kalshi and reading
forty ladders by hand.

**3. The nav order does not match how the pages are used.** Screen is last, below
Status, despite being the second page opened every day. And "Screen" describes a
mechanism, not what the page is for.

## Scope

**In:** a new Timeseries page; a new unsettled-markets table on the Strategy
page; the nav reorder and the display rename.

**Out, deliberately:**

- No screening rule changes. `screen_rules` is not touched. The storm gate, the
  strength floor and the settlement-truth audit found in the 2026-08-16 outcome
  review are a separate piece of work and must not ride along here — mixing them
  in would make `screen_score`'s record incomparable across the change.
- No new log, no new Action step, no new data-branch document. Everything below
  reads a feed that already exists or a document already published.
- No module renames. `screen_view.py` and its siblings keep their filenames.

---

## A. The Timeseries page

### Source

`weather.gov/wrh/timeseries` is a Highcharts front end over the **Synoptic Data
v2 API** (`api.synopticdata.com/v2/stations/timeseries`), authenticated with a
token NWS embeds in `/source/wrh/apiKey.js`. We do not use it. That token is
theirs, Synoptic's free tier would need a new secret, and — per
`current-temp-freshness` — Synoptic draws the same MADIS pipeline and is whole-°C
anyway.

`api.weather.gov/stations/{ICAO}/observations` **is** that feed. Verified live
2026-08-16 against KLAS: a 36-hour window returned **466 readings**, five minutes
apart. Free, no key, already the source `screen.py` and `nws_observations.py`
read.

### Where the fetch lives

A windowed, ICAO-addressable function in `sources/nws_observations.py`:

```python
def window_for_id(station_id: str, start: datetime, end: datetime,
                  ttl: int = 300, fetch=None) -> list:
    """Every reading this station published in [start, end], newest first."""
```

Addressable by raw ICAO rather than by station code, because `config` only knows
KDFW and KAUS while this page covers twenty cities. That is the same split
`metar_tgftp.latest_for_id` already makes for exactly this reason: ICAO in, UTC
out, the caller localises.

`screen.fetch_observations` is the same request with a different signature. It is
left alone — it is on `screen_alert`'s 5-minute loop with its own 300s TTL, and
merging the two would couple a display page to the alert path.

**Pagination:** 466 readings in 36 hours is close to the `limit=500` ceiling, and
a station having a bad day emits SPECIs on top of the routine cadence. The
function requests `limit=500` and, if it gets 500 back, issues one more request
for the remainder of the window and concatenates. One extra call in the rare
case, never in the common one.

### The page

`timeseries_view.py`, rendered by a `timeseries_page()` in `app.py`.

- **City dropdown** over all twenty `hourly_cities` keys, `key="timeseries_city"`.
  Deliberately not `city_view.city_control` — that is the sticky Dallas/Austin
  pick shared by the modelled pages, and choosing Las Vegas here must not follow
  the user to Forecast. Same reasoning `hourly_page` already documents.
- **Climate-day extremes above the table.** Running max and min for the climate
  day in progress, folded over the readings on screen, with the time each
  occurred. This is the number the market settles on, and it is free from data
  already fetched. The day boundary comes from `hourly_cities.climate_day`, the
  one authority for it — a fixed-LST window, not local midnight.
- **The table**, newest first, every timestamp in the city's own zone: Time,
  Temp, Dew point, Wind, Raw METAR. Hand-rolled HTML through `market_view`'s
  `.wtbl` / `.wtbl-wrap`, like every other table in this app.
- **36-hour window.** Older readings are not fetched, so nothing needs to fall
  off — there is no store to trim.
- **Cadence.** `st_autorefresh(interval=300_000)` with a matching 300s cache TTL.

### The precision marker

Two kinds of row arrive in one feed and they are not equally precise:

| | cadence | precision | `rawMessage` |
|---|---|---|---|
| 5-minute MADIS | :00, :05, :10 … | whole °C | empty |
| routine METAR | ~:53 | tenths °C, via the `T` group | present |

Observed in the same KLAS payload: `39`, `38`, `38` on the 5-minute rows against
`37.8` and `RMK AO2 SLP136 T03780111` on the :53 row.

The whole-°C rows render at the **bottom** of their bucket — 38 °C displays as
100.4 °F when the true reading is anywhere up to 102.2 °F. That is up to 1.8 °F
low, and it is precisely the 100-vs-101 wall in `kalshi-cli-settlement-basis`: a
whole-°C feed cannot represent 101 °F at all.

So the hourly rows are marked, and a caption states the asymmetry. Unmarked, the
table would invite exactly the misreading that cost the KAUS 2026-08-16 episode —
a 5-minute view showing 100 while the METAR read 102.02 and the market had
already repriced.

The extremes above the table are computed from the values as published. They are
therefore a floor on the true max and a ceiling on the true min, and the caption
says so rather than the page implying a precision the feed does not carry.

---

## B. The unsettled-markets table

### The rule

For each of the forty city×variable series, on the climate day **in progress**:
take the highest-priced open bracket. If its YES ask is below **0.90**, the
market has not collapsed onto one outcome — it is unsettled, and it gets a row.

Today only. Tomorrow's ladders are unsettled almost by definition and would
double the table with rows that carry no information.

### Where it is computed

In `screen.py`, published into `screen_reference.json`. Not at page load.

`screen_pass` already calls `list_markets(series, status="open")` for every
series on every firing, so the leader costs nothing there — it is a reduction
over rows already in hand. Pricing forty ladders at page load would cost 20–30
seconds under the 0.5s spacing `REQUEST_SPACING_S` documents as necessary, on
every rerun, including a theme toggle.

New per-series key, alongside `days` / `realized` / `remaining`:

```python
reference[series]["leader"][day.isoformat()] = {
    "ticker": ..., "label": ..., "price": ...,      # the top bracket
    "next_label": ..., "next_price": ...,           # the second-highest, or None
}
```

The runner-up is carried as its own label/price pair rather than a nested dict,
so a consumer reading one field cannot accidentally read the leader's.

`merge_reference` carries only `station` and `timezone` forward, and this is a
measurement, not an identity — so it is dropped from a carried-forward city
exactly like `realized` and `remaining`, for the reason that function documents:
a stale price is worse than no price.

### The table

On the Strategy page, above the fade table — "where is there a market at all"
precedes "which bracket is mispriced". Columns: City, Var, leading bracket, its
price, the runner-up, and the Ref and consensus values already in the reference
and consensus documents.

Captioned with the reference's `generated` stamp. These prices are as of the last
firing, which is 30–60 minutes, and the page must say so — the same convention
the existing Price column follows against its live `YES Now` companion.

### Why 0.90 and not a live re-quote

The threshold is the user's, and it is a screening question rather than a trading
one: at 90¢ the market has picked its answer. A live re-quote per row would
reintroduce the forty-call cost for a table whose whole purpose is orientation.

---

## C. Nav and the rename

```
Forecast · Strategy · Hourly · Timeseries · Journal · History
         · Trader · Edge · Lab · Accuracy · Status
```

Two display strings change: the `st.Page` title, and `screen_view.render`'s
`st.subheader("Screen — Mispriced Brackets")` → `"Strategy — Mispriced
Brackets"`.

`screen_view.py`, `screen_rules.py`, `screen.py`, `screen_alert.py`,
`screen_forecast.py`, `screen_score.py`, `screen_pnl.py` keep their names. The
modules are named for the mechanism, which has not changed; renaming eight files
and their tests would churn history and rewrite every import for a label.

---

## Testing

Pure functions, no network, matching the existing suite:

- `window_for_id` pagination: a 500-row first response triggers exactly one
  follow-up; a short response triggers none.
- Row classification: a row with a `rawMessage` is marked hourly, one without is
  marked 5-minute.
- Climate-day extremes fold only readings inside the fixed-LST window, and a
  reading in the 00:00–00:59 DST hour lands on the previous day.
- Leader selection: highest price wins; ties resolve deterministically; a series
  with no open market yields no entry.
- The 0.90 threshold: 0.89 is unsettled, 0.90 and 0.91 are not.
- `merge_reference` drops a carried city's `leader`, as it drops `realized`.

## Risks

- **Feed lag.** These readings publish ~20 minutes after the fact
  (`current-temp-freshness`, irreducible). The page shows observation time, not
  fetch time, so a glance cannot mistake the two.
- **A dead station.** One city's fetch failing must show an empty table with a
  notice, never a stack trace — the `source-outage-resilience` convention.
- **Reference growth.** Forty leader entries add roughly 4 KB to a document
  rewritten every firing. Negligible against the existing `days` / `realized` /
  `remaining` blocks.
