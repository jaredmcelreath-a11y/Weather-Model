# Kalshi multi-city price scanner: is the tail overpriced, and where?

Date: 2026-08-03
Status: approved, ready for implementation plan

## Why

The autonomous trader buys favorites: `entry_allowed` requires the model and the
market to agree within 1°F, so a large model-vs-market divergence is what *blocks*
a trade. Measured over its whole shadow history that configuration runs 8W/9L with
an average win of +0.20 and an average loss of −0.35 — it needs a ~64% hit rate to
break even and is at 47%.

The opposite trade looks better shaped. Fading an overpriced bracket — the market
asks 35% on a low above 72 that the forecast makes very unlikely — pays 0.35 to
risk 0.65, and KDFW tail calibration says the model's low probabilities are sound:

| model says | actually settled | n bins |
|---|---|---|
| 1–5%  | 2.25% | 89 |
| 5–10% | 0.00% | 44 |
| 10–20% | 9.09% | 44 |
| 20–35% | 20.73% | 82 |

(same-day, CLI basis, 92 day-variables from `forecast_log`)

There is also a structural reason to expect this to persist: the favorite-longshot
bias, one of the more durable findings in betting-market research, says longshots
are systematically overpriced.

But the trader cannot make that trade — it only ever evaluates the bracket
containing the market's EV, it is YES-only by design (`size_bracket`), and its edge
check is currently disabled (`require_edge: False`). Building the capability means
either a second bot or a significant rework, and either way it wants a model per
city. Kalshi lists ~20 cities; a per-city model means per-city settlement-basis
verification, convective county research, and weeks of calibration accumulation,
all gated behind Open-Meteo rate limits that already 429 us at two cities.

**So measure the opportunity before building anything that trades it.** The
question "are Kalshi weather tails overpriced, in which cities, and by how much"
can be answered with Kalshi data alone — no model, no station config, no weather
feeds — because Kalshi self-reports settlement: every contract carries
`result: "yes"|"no"` and `status: "finalized"`.

## What this is not

Read-only. It places no orders, imports nothing from `trader.py` or
`kalshi_orders.py`, and writes no trade state. It cannot affect the live trader.

It is also not a signal generator. It produces a historical reliability curve, not
live trade flags — without a per-city model there is no defensible threshold for
calling a specific bracket "rich" in real time, and any such flag is unvalidated
until settlement anyway.

## Architecture

Three new modules, following the existing schema / IO / report split:

- **`scanner.py`** — orchestration. Two entry points: a *snapshot pass* (discover
  series, fetch ladders, append rows) and a *settlement pass* (fetch finalized
  markets, append outcomes).
- **`scan_log.py`** — record schema and git-backed IO, mirroring
  `trade_log.py` + `trade_state.py`.
- **`scan_report.py`** — the reliability curve.

Plus two additions to `sources/kalshi.py`: list series by category, and list
markets by series. Both take an injectable `fetch` like `fetch_orderbook` does.

## Series discovery

Automatic, not a hardcoded city table. Query the "Climate and Weather" category,
keep series whose ticker starts with `KXHIGH` or `KXLOW` **and** that have at
least one market either currently open or with a `close_time` within the last 7
days.

This matters because the live series list is full of legacy duplicates —
`KXHIGHDEN` and `KXHIGHTEMPDEN`, three Houston variants (`KXHIGHOU`,
`KXHIGHHOU`, `KXHIGHTHOU`), `KXHIGHNY` beside `KXLOWNYC` — and the naming is
inconsistent even within a city (`KXHIGHAUS` but `KXLOWTAUS`). A hardcoded table
would encode today's mess and rot. The activity filter resolves it and keeps
working when Kalshi renames things.

`KXHIGHUS` (national) and `KXHIGHNYD` (hourly directional) are excluded: they are
not daily city high/low markets.

## No per-city timezone config

Snapshots anchor to **hours before each market's own `close_time`**, which Kalshi
supplies per market, rather than to local clock time. A single UTC firing yields a
different — and recorded — `hours_to_close` per city.

This removes the only per-city data the scanner would otherwise need, and it is
the better analytical axis anyway: "12 hours before settlement" is comparable
across cities in a way that "13:00 local" is not.

## Data model

Two files on a dedicated branch, joined on ticker at report time — the same shape
as `betting_log.jsonl` + `settlements.jsonl`.

Snapshot row, one per bracket per firing:

```json
{"ts": "2026-08-03T19:00:00Z", "series": "KXHIGHDEN", "variable": "high",
 "ticker": "KXHIGHDEN-26AUG03-B72.5", "floor": 72, "cap": 73,
 "strike_type": "between", "yes_bid": 0.33, "yes_ask": 0.37,
 "volume": 120, "close_time": "2026-08-04T06:00:00Z", "hours_to_close": 11.0}
```

Settlement row, one per ticker, appended once:

```json
{"ticker": "KXHIGHDEN-26AUG03-B72.5", "result": "no",
 "settled_at": "2026-08-04T12:00:00Z"}
```

`variable` is derived from the series prefix (`KXHIGH*` → high, `KXLOW*` → low).
City display names are resolved at report time from the live series endpoint, not
stored per row — 55k rows/month do not each need to carry "Highest temperature in
Denver".

Prices come from the markets endpoint (`yes_bid` / `yes_ask`), not the order book:
one call per series, ~40 calls per firing, ~120/day. No rate-limit concern, and
nothing touches Open-Meteo.

## Report

A reliability curve: of brackets priced in a given band, what fraction actually
settled YES?

Bucketed by city × variable × price band, sub-bucketed by `hours_to_close`
(0–6h, 6–18h, 18–36h) to answer whether the bias is larger day-ahead (thin book)
or near settlement.

Two prices are reported per band:

- **mid** — `(yes_bid + yes_ask) / 2`, the fair-value question: is this market
  well calibrated?
- **NO cost** — `1 − yes_bid`, what a fade would actually have paid, since you
  cannot trade the mid.

The mid-based curve leads, because the first question is whether a bias exists at
all; the cost-based column says whether it survives the spread.

Every row reports both `n_observations` and `n_unique_brackets`. Snapshotting the
same bracket three times a day produces three correlated observations of one
outcome, and a curve that reported only `n_observations` would overstate its own
significance.

## Storage and scheduling

A dedicated `scan-data` branch, isolated exactly as `trade-data` is, reusing the
existing GitHub-contents IO pattern.

`.github/workflows/scan.yml`: snapshot firings at **12:00, 18:00 and 00:00 UTC**,
plus a settlement pass at 12:00 UTC. US city markets close near 06:00 UTC (the
climate-day end), so those firings land roughly 18h, 12h and 6h before close —
spanning day-ahead through near-settlement.

Volume: ~20 cities × 2 variables × ~15 brackets ≈ 600 rows per firing, ~1,800
rows/day, ~55k rows/month (~6 MB). Comparable to the existing logs.

GitHub's scheduler is best-effort and throttles tight crons — a snapshot that
fires late is harmless here, because `hours_to_close` is recorded per row rather
than assumed from the schedule.

## Testing

Unit tests against mocked API payloads, using the injectable-`fetch` pattern
`sources/kalshi.py` already uses:

- discovery filter keeps active daily city series and drops legacy duplicates,
  `KXHIGHUS`, and `KXHIGHNYD`
- row construction, including `hours_to_close` and `variable` derivation from the
  series prefix
- settlement join, including a ticker snapshotted three times mapping to one
  outcome
- reliability bucketing, including the `n_observations` vs `n_unique_brackets`
  distinction
- a market with no quotes (`yes_bid`/`yes_ask` null) is skipped, not recorded as
  price 0

## Known limits

**Top-of-book only.** The markets endpoint gives best bid/ask, not depth. A tail
showing a fat edge against two resting contracts is not a real opportunity, and
this design cannot tell the difference. Deferred to a later phase; the report must
not be read as tradeable size.

**Correlated observations.** Three snapshots of one bracket are not three
independent trials. Reported, not corrected for.

**Settlement is Kalshi's, not NWS's.** That is the point — it is what the contract
actually paid — but it means the scanner cannot detect a Kalshi settlement error,
and it carries no information about the underlying weather.

**Measurement only.** A positive result does not imply a tradeable strategy: it
would still need a per-city model to identify *which* specific bracket is
overpriced on a given day. This tells you whether that work is worth doing, and
which cities to do it for first.

## Out of scope

Order-book depth; live candidate flags; any trading logic; any new station
`StationConfig`; any UI page. If the curve shows a real bias, the follow-on work
is a separate spec.
