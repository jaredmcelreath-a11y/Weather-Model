# CLI Report Alert — Design

**Date:** 2026-07-20
**Status:** Approved, ready for planning

## Goal

When NWS issues the afternoon **CLIDFW** daily climate report (~4:41 PM CDT), which
reports the day's now-locked high and low, surface it two ways:

1. **On-page box** on the Forecast page confirming the official high/low.
2. **One-time ntfy push** to the user's phone.

Both are **minimal**: the day's high, low, and the issuance time. No bracket
resolution, no market/edge callout (explicitly out of scope for v1).

By ~4:41 PM both the climate-day high (afternoon peak) and low (dawn minimum) are
effectively settled, so this is the moment the Kalshi settlement values are known.

## Source

NWS API, two calls (public, no auth; send a `User-Agent` with a contact email):

1. `GET https://api.weather.gov/products/types/CLI/locations/DFW`
   → `@graph` list of recent products, newest first, each with `@id` + `issuanceTime`.
2. `GET <newest @id>` → `{ issuanceTime, productText }`.

Location code is **`DFW`** (not `FWD`). NWS issues multiple CLI products per day
(e.g. ~00:31 and ~06:45 UTC report the *prior completed* day; ~21:41 UTC = 4:41 PM
CDT is today's afternoon preliminary). Corrected reissues carry the same date.

### Product text structure (captured 2026-07-20, use as test fixture)

```
000
CDUS44 KFWD 202141
CLIDFW

CLIMATE REPORT
NATIONAL WEATHER SERVICE FORT WORTH TX
441 PM CDT MON JUL 20 2026

...................................

...THE DALLAS/FORT WORTH CLIMATE SUMMARY FOR JULY 20 2026...
VALID AS OF 0400 PM LOCAL TIME.
...
WEATHER ITEM   OBSERVED TIME   RECORD YEAR NORMAL DEPARTURE LAST
                VALUE   (LST)  VALUE       VALUE  FROM      YEAR
...................................................................
TEMPERATURE (F)
 TODAY
  MAXIMUM        100    254 PM 109    2022  96      4       95
  MINIMUM         80    615 AM  65    1920  76      4       80
  AVERAGE         90
```

### Parse rules

- **Report date**: from the `...CLIMATE SUMMARY FOR <MONTH DAY YEAR>...` line
  (e.g. `JULY 20 2026`). This is the day the report is *for*.
- **High / low + times**: within the `TODAY` block, first
  `MAXIMUM <int> <time AM/PM>` and `MINIMUM <int> <time AM/PM>` lines. The
  observed value is the first integer after the label; the time is the next
  `\d{1,4}\s*[AP]M` token. (Do not confuse with the RECORD value further right.)
- **Issued**: `issuanceTime` from the product JSON (ISO, UTC).

"**Today's report is in**" ≡ parsed report date == today's **climate day**
(`settlement.climate_day_of(now)`). Because the overnight/AM issuances are dated
the prior day, the first product dated *today* is the afternoon one — no need to
inspect the issuance hour.

## Components

### `sources/nws_cli.py` (new)

```
fetch_latest_cli(ttl: int | None = None) -> dict | None
    # {report_date: date, high_f: int, low_f: int,
    #  high_time: str, low_time: str, issued: datetime} or None
```

- Uses `sources/common.get_text` (or the module's existing fetch helper) with a
  short live TTL for callers that want fresh data.
- Returns `None` on any network/parse failure or if the product can't be parsed.
- A pure `parse_cli(text: str, issued: datetime) -> dict | None` helper does the
  text parsing so it can be unit-tested against the fixture without a network call.

### `notify.py` (new, tiny)

```
send_ntfy(title: str, message: str) -> bool
```

- `POST https://ntfy.sh/<topic>` with `Title` header = `title`, body = `message`.
- Topic from env `NTFY_TOPIC` (bare topic name or full URL). If unset → no-op,
  return `False` (so local runs without the secret don't error).
- Best-effort: swallow exceptions, return success bool.

### On-page box — `app.py` Forecast page

- Fetch `nws_cli.fetch_latest_cli(ttl=~300)`.
- If it returns a report whose `report_date` == today's climate day, render a
  small box:
  `Official NWS climate report — 4:41 PM · High 100°F · Low 80°F`
  (format the issuance time in local; use `high_time`/`low_time` if desired).
- Otherwise render nothing. Never blocks or crashes the page on fetch failure.

### Push — `scheduled_log.py`

- New best-effort step `_maybe_alert_cli(now)` in the scheduled run:
  1. `cli = nws_cli.fetch_latest_cli(ttl=live)`; if `None` or not dated today → return.
  2. Load `cli_alert_state.json` (`{"last_alerted_day": "YYYY-MM-DD"}`).
  3. If `last_alerted_day` == today → already pinged, return.
  4. `notify.send_ntfy("Dallas Climate Report",
       f"High {high}°F · Low {low}°F · issued {time}")`.
  5. On success, write `last_alerted_day = today` to the state file.
- Wrapped so any failure just logs and is skipped, like the other steps.

### State — `cli_alert_state.json` on the `data` branch

- Small JSON persisting `last_alerted_day`. Guarantees exactly one push per day
  across the 10-min cron cadence. Lives on the `data` branch alongside the other
  logs.

### Workflow — `.github/workflows/log.yml`

- **Restore** step: add
  `git show origin/data:cli_alert_state.json > cli_alert_state.json 2>/dev/null || true`.
- **Publish** step: add `cli_alert_state.json` to the temp copy + `git add -f`.
- Pass the ntfy topic to the run: `env: NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}`
  on the "Append this snapshot" step (or job-level).

## Data flow

- **Box**: live NWS fetch per page load (cached ~300s), independent of the push
  state. Shows for the rest of the day once today's report is dated today.
- **Push**: the every-10-min Action detects today's report on its first run after
  ~4:41 PM, sends one ntfy, and records the day so later runs stay quiet.

## Error handling

Everything is best-effort and must never crash the page or the scheduled run:
network error, HTTP error, unparseable product, or missing `NTFY_TOPIC` → box
hidden / push skipped, logged, execution continues.

## Testing

- **Parser** (`parse_cli`) against the captured fixture:
  - correct `high_f=100`, `low_f=80`, `high_time="254 PM"`, `low_time="615 AM"`,
    `report_date=2026-07-20`.
  - a prior-day (early-AM) product fixture → report date is the prior day
    (so the caller treats it as "not today").
  - malformed / truncated text → `None`.
- **Once-per-day gate**: simulate two runs with today's CLI in →
  `send_ntfy` called once; state advances; a run on the next day fires again.
  (`send_ntfy` mocked.)
- **`send_ntfy`**: no-op returning `False` when `NTFY_TOPIC` unset; POSTs with the
  right title/body when set (network mocked).

## Out of scope (v1)

- Bracket resolution / market-edge callout in the box or push.
- Separate high vs low pings (one combined afternoon alert).
- Driving any settlement/lock logic — the existing daily-summary flooring already
  handles bounds; this feature is confirmation + notification only.
```

## Decisions locked in

- Push channel: **ntfy**, title **`Dallas Climate Report`**.
- Box + push content: **minimal** (high, low, issuance time).
