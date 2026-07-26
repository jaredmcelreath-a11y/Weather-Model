# Austin (KAUS) settlement-basis verification — 2026-07-26

Blocking Task 1 of Austin Plan 2. All facts verified against live NWS + Kalshi
data (not assumed). Mirrors the KDFW basis rigor in
`docs/benchmarks/2026-07-14/climate-day/`.

## Settlement station: Austin-Bergstrom (KAUS) — CONFIRMED

The live CLIAUS product header reads:

```
CLIAUS
CLIMATE REPORT
NATIONAL WEATHER SERVICE AUSTIN/SAN ANTONIO   (office KEWX)
...THE AUSTIN BERGSTROM CLIMATE SUMMARY FOR JULY 26 2026...
VALID AS OF 0700 AM LOCAL TIME.
```

The Kalshi market rules confirm the same source (market `KXHIGHAUS-26JUL27-T96`):

> "If the highest temperature recorded in **Austin Bergstrom** for July 27, 2026
> as reported by the **National Weather Service's Climatological Report (Daily)**,
> is less than 96°, then the market resolves to Yes."

So Kalshi settles Austin on **CLIAUS at Austin-Bergstrom (KAUS)** — NOT Camp
Mabry (KATT). The user's recollection was correct. Config `id="KAUS"`,
`cli_location="AUS"`, lat/lon 30.1975 / -97.6664 (Bergstrom) are correct.

## CLI product & climate day

- Product: **CLIAUS**, NWS location code **AUS**, issuing office **KEWX**
  (Austin/San Antonio). `nws_cli.list_url("KAUS")` →
  `https://api.weather.gov/products/types/CLI/locations/AUS` (28 products
  returned; parses fine after the parser fix below).
- Climate day: **fixed Local Standard Time**, midnight-to-midnight — the CLIAUS
  record times are printed "(LST)" and the summary is "VALID AS OF 0700 AM LOCAL
  TIME", the same convention CLIDFW uses (verified 2026-07-14). No station-
  specific climate-tz change needed; KAUS keeps `Etc/GMT+6`.

## Parser fix (discovered)

`fetch_latest_cli(station="KAUS")` initially returned `None`: the two issuing
offices format the time column differently.

- CLIDFW (NWS Fort Worth, KFWD): `MAXIMUM  98  519 PM` — no colon.
- CLIAUS (NWS Austin/San Antonio, KEWX): `MAXIMUM  78  12:20 AM` — colon.

`parse_cli`'s `_MAX_RE`/`_MIN_RE` matched only the no-colon form. Fixed the time
group to accept both (`(\d{1,2}:\d{2}|\d{1,4})`). Verified live afterward:
- CLIAUS → high 78, low 73 (times "12:20 AM" / "5:59 AM").
- CLIDFW → high 98, low 80 (times "519 PM" / "649 AM") — unchanged.

## Kalshi series tickers — ASYMMETRIC

Probed `GET /trade-api/v2/markets?series_ticker=…&status=open`:

| Variable | Series ticker | Open markets | Example event |
|----------|---------------|--------------|---------------|
| High | **`KXHIGHAUS`** (no "T") | 3 | `KXHIGHAUS-26JUL27` |
| Low  | **`KXLOWTAUS`** (with "T") | 3 | `KXLOWTAUS-26JUL27` |

Both the high and low markets exist (confirming "high and low"). Note the
inconsistency: Austin's **high dropped the `T`** that Dallas keeps
(`KXHIGHTDAL`/`KXLOWTDAL`). A pattern-based guess would have been wrong on the
high — which is why `StationConfig` stores the two series tickers explicitly
(`kalshi_high_series` / `kalshi_low_series`). The event-suffix format
(`%y%b%d` → `26JUL27`) is shared, so `kalshi._event_suffix` needs no change.

## Config values landed (Task 1)

```
KAUS: id=KAUS, cli_location=AUS, lat=30.1975, lon=-97.6664, climate_tz=Etc/GMT+6,
      kalshi_high_series=KXHIGHAUS, kalshi_low_series=KXLOWTAUS
```

Still open for later Plan 2 tasks: Austin convective county map (Task 2),
kalshi.py `station` threading (Task 3), scheduled_log/log.yml (Tasks 4–5).
