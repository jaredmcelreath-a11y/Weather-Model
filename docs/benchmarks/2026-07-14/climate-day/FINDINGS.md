# CLI climate-day verification — VERIFIED 2026-07-14

The open question from `settlement.py`'s docstring (and the 2026-07 audit, item 4):
does the NWS CLI climate day run midnight-to-midnight clock time (what the model
uses) or Local Standard Time? Answered empirically.

## Verdict

**The CLIDFW climate day is midnight-to-midnight LOCAL STANDARD TIME (UTC−6
year-round) — i.e. 1:00 AM → 1:00 AM CDT during daylight saving.** The model's
`local_day_bounds` (clock midnight → midnight) is offset by one hour from the
settlement window from March to November. In winter (CST) the two coincide.

**Our settlement truth is already correct:** IEM `daily.py` (what
`fetch_actual_cli` / `settlements.jsonl` use) matches the CLI product exactly on
the discriminating day — so the scoring history, correction estimators, and
settlement offsets are all on Kalshi's basis. The gap is only in the LIVE
model's window logic.

## Evidence

Scanned KDFW 5-min obs 2026-03-09 → 2026-07-12 (DST season) for days whose
clock-window daily min occurred 00:00–00:59 CDT (the boundary zone where the
two conventions disagree). Two candidates; one discriminates:

**May 26/27, 2026 (the smoking gun):**

| source | May 26 min | May 27 min |
|---|---|---|
| clock-midnight window (5-min obs) | ~68 (dawn) | **67.0 @ 00:53 CDT** |
| LST window (5-min obs) | includes the 00:53 CDT reading | 68.0 |
| **CLIDFW product** | **67 @ "1159 PM" (LST)** | 67 @ "408 AM" (LST) |
| IEM daily.py | **67** | 67 |

May 26's official report claims a minimum of 67 at 11:59 PM *LST* — which is
**12:59 AM CDT on calendar May 27**. A reading after clock midnight settled the
*previous* day's market. (May 27 separately re-touched 67 at dawn on the CLI's
1-minute data, which is why its own value doesn't discriminate.) IEM daily.py
reports 67 for May 26, provable only via the LST window — daily.py is
LST-windowed, matching Kalshi settlement.

Also confirmed: every CLIDFW time column is headed "(LST)" year-round (e.g. the
Jul 13 report: min 72 @ "508 AM" LST = 6:08 AM CDT).

## Consequences for the model (DST season only)

1. **Evening end under-coverage (the one that costs money):** today's Kalshi
   low/high can still be set by readings 00:00–00:59 CDT *tomorrow*. The model
   stops looking at midnight — a post-midnight cold push (exactly the
   front/storm-night pattern; May 26 is a real settled example) can settle a
   bin below a "locked" low the model was ~95% confident in. The front guard's
   scan also stops at clock midnight, so it under-projects by the same hour.
2. **Morning start over-coverage:** readings 00:00–00:59 CDT today belong to
   *yesterday's* market; the model wrongly ingests them into today's observed
   extremes and hard bound. Usually harmless (dawn goes lower anyway), but it
   can wrongly tighten the bound on a warm-then-cooling night.
3. **Frequency:** 2 boundary-min candidates in 126 DST days scanned (~1.6%),
   one of which moved the settled value by 1°F. Rare — but concentrated on
   precisely the volatile nights where bets are most exposed, and it will be
   more common in fall front season.
4. **A side opportunity:** between 12:00 and 12:59 AM CDT, *yesterday's* Kalshi
   market is still unsettled and the remaining risk window is minutes long —
   near-certain information if the market is still quoting.

## Fix directions (not yet built — needs design)

- **A. Global LST window:** point `local_day_bounds` at a fixed `Etc/GMT+6`
  zone. Cleanest semantics (the whole pipeline — obs windows, locks, member
  extremes, `covers_extreme`, charts — moves to the settlement day), but
  touches everything, incl. the hourly-basis history and the lead-bucket day
  math; needs careful review of hour-of-day assumptions (`_LOW_WINDOW` etc.).
- **B. CLI-scoped window:** thread a settlement-day variant only through the
  CLI paths (obs extremes, locks, front-guard scan, hard bound). Smaller blast
  radius, but two windows coexist in one pipeline.
- Either way the change is inert in winter and self-tests against May 26.

## Reproduction

- Probe script: scratchpad `climate_day_probe.py` (scan 5-min obs for boundary
  days under both windows).
- CLI products: `https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil=CLIDFW&sdate=2026-05-27&edate=2026-05-29&fmt=text&limit=9`
- IEM daily: `daily.py?network=TX_ASOS&stations=DFW&...&year1=2026&month1=5&day1=25...`
