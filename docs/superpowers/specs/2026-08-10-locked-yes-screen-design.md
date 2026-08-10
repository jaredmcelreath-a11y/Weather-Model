# Underpriced YES: brackets the day has already won

Date: 2026-08-10

## Problem

The Screen has two rules and both are fade-shaped. `dead_candidate` can kill a
bracket but never confirm one; `forecast_candidate` looks for brackets FAR from
the forecast. Nothing fires on "this looks won, and it is cheap."

The case that exposed it, live on 2026-08-09 at 17:18 LST:

| KXLOWTPHX-26AUG09-T91, "92° or above" | |
|---|---|
| Realized low so far | **93.0 °F** at 06:51 LST (223 readings, corroborated by five more at 93.2) |
| Remaining forecast | 110 → 109 → 107 → 103 → 101 → 99 → 97 → **96 at midnight** |
| Left in the climate day | ~6.7 h (close 07:00Z = midnight LST, matching the ticker's day) |
| Market | bid **36¢** / ask **39¢**, volume 9,382 |
| Neighbour "90° to 91°" | 34¢/42¢ — the market's favourite |
| Storm chance, low window | 20% (POP peaks 8–9pm) |

The market's implied low is 91. The minimum already happened at 93.0, and
nothing in the remaining forecast comes within four degrees of threatening it.
The screen said nothing, because it has no rule of this shape.

## What this is not

**Not "already settled."** That framing holds for a high and fails for a low. A
low can only fall, so a bracket unbounded UPWARD — "92 or above" — never becomes
physically certain while the day runs. Six hours remained in which a monsoon
downdraft could crash 109 °F to below 91.5. The 20% POP is a real risk and it is
part of what the market is charging for; it is simply nowhere near enough risk
to justify 39¢.

That asymmetry is why this cannot ship as a one-line mirror of `dead`, and why
it splits into two rules with different standards of evidence.

## Scope

**In:** two new rules, a second table, a second log, and alerts on both.

**Out, deliberately:**

- `screen_rules.dead_candidate` and `forecast_candidate` are not modified.
- `screen_score` is not touched **at all** — not even a filter. The fade side's
  66-settled record stays exactly as comparable as it is today. This is the
  reason for a separate log rather than a `side` field (see "Why a separate
  log").
- No calibrated probability. The rules report a MARGIN in degrees, as the fade
  side reports a gap, for the reason `screen_rules` already documents.

## The two rules

Both live in `screen_rules.py` beside their mirrors, reusing `winning_range`,
`settled_range`, `bracket_gap` and the price helpers. Both return the existing
candidate dict shape.

### `locked_candidate` — HARD, pure physics

Fires only on a tail left open in the direction the extreme can still move:

- **LOW**, winning range `(None, hi)` — "83° or below". Certain once
  `highest_settled <= hi`. The low can only fall, so it stays won.
- **HIGH**, winning range `(lo, None)` — "91° or above". Certain once
  `lowest_settled >= lo`.

Compared against `settled_range(bound)` — the whole-°F CLI basis with
per-reading whole-°C slack — never the raw reading. Without that it would claim
certainty an observation cannot carry, which is exactly the bug that called
Atlanta's "72 to 73" low dead on 2026-08-06 off a realized 71.6 while Kalshi had
it at 91% YES.

`margin` = how far past the strike the settled value already sits:

```
LOW    margin = hi − highest_settled
HIGH   margin = lowest_settled − lo
```

Logged `kind` is `"locked"`.

### `guarded_candidate` — SOFT, forecast-locked

Logged `kind` is `"guarded"`.

Two conditions:

1. The realized extreme already sits inside the winning range
   (`bracket_gap(row, bound) == 0`).
2. The remaining forecast keeps it there by at least `MIN_CANDIDATE_GAP_F`
   (4.0 °F).

**Each variable has exactly one threatened edge**, which makes the two rules
partition cleanly rather than overlap:

```
LOW    margin = min(remaining forecast) − lo     # lo is None  -> nothing to
                                                 #   threaten -> locked_candidate
HIGH   margin = hi − max(remaining forecast)     # hi is None  -> likewise
```

A low can only fall, so the only way to lose a low bracket is to drop below its
`lo`; it can never rise out of the top. A high is the mirror.

"Remaining forecast" is `screen_forecast._still_open`, which already encodes
that a low's window runs to midnight precisely because an evening downdraft can
crash it. It is promoted to a public `still_open(day_periods, variable)`, and a
new `remaining_extreme(periods, day, tzname, variable, now)` reduces it to the
single number both `screen.py` and the rule need.

Phoenix: realized 93.0 inside `(92, None)`; remaining bottoms at 96;
margin `96 − 92 = 4.0`; fires, just clearing the bar.

### Why the margin bar is flat 4.0 and not `required_gap`

`required_gap` scales by forecast error AT THE LEAD WHERE THE EXTREME FORMS —
for a same-day low it demands 9.7 °F. That is the right bar for the fade screen
and the wrong one here: the extreme has ALREADY FORMED, and the only question
left is whether the remaining hours can undercut it. That is a short-range,
convection-dominated risk, not general forecast error. Applying 9.7 would
silence Phoenix and every case like it.

So: the flat `MIN_CANDIDATE_GAP_F` the screen already treats as its base unit,
with `Storm` shown on the row, and the log to calibrate it later. Inventing a
second sigma with no data behind it is the season-readiness phantom-edge bug.

### The price gate

These are YES buys, so the cost is the **YES ask**, and the band is
`MIN_LIVE_YES_PRICE = 0.20` to `MAX_LIVE_YES_PRICE = 0.90`.

One band, applied in all three places, so a row cannot be logged under a
standard the page then disagrees with: at firing (`screen.py`, against the
row's `yes_ask`), on the live loop (`screen_alert`), and at page load
(`screen_view`). It replaces `_tradeable_price`'s role on this side entirely —
`MIN_CANDIDATE_PRICE`/`SETTLED_PRICE` are fade-side firing gates and are not
reused here. Reading the live YES ask needs a `yes_ask_of(market)` mirroring
`no_ask_of`: Kalshi's `yes_ask_dollars` when present, else `1 − no_bid`, with
the same dollar-STRING parsing. An unquoted row survives, as it does on the fade
side.

The cap is obvious: above it the market agrees and under 11% is left. The floor
carries the fade side's meaning, mirrored — **below 20¢ the market is saying the
screen's REFERENCE is wrong, not that the price is.** A supposedly hard-locked
bracket trading at 5¢ is far likelier to mean a bad station reading than free
money, and that is precisely when this screen should not shout. Phoenix at 39¢
sits comfortably inside.

## Why a separate log

`screen_score.score()` computes a NO-cost per row via `scan_report.no_cost` and
a hit rate against it, and `by_kind` groups on `kind`. Every YES row entering
`scan_candidates.jsonl` would have fade math applied to it.

A `side` field plus a filter would work, but it puts one line between the fade
track record and corruption, and legacy rows carry no `side` key so the filter
must treat absent as NO. Physical separation costs one extra contents-API read
on page load and cannot go wrong.

`scan_locked.jsonl`, daily partitions, same `append_many`.

## What is displayed

A second table under the candidate table and above the consensus board:

```
City · Day · Var · Bracket · Price · YES Now · Margin · Kind · Storm · Hrs
```

Every column means something for a YES row — unlike `Gap` and `Str`, which
measure distance from the reference to the bracket and are identically zero
here. `Price` is the row's `yes_ask` at firing, up to 30 minutes old; `YES Now`
is `yes_ask_of` live at page load, the mirror of `NO Now`. `Margin` is the
degrees defined per rule above, `Kind` is the rule that fired.

`Kind` reads **Locked** or **Guarded**. Deliberately not "dead"/"forecast": the
two logs would otherwise both carry a kind called "forecast" meaning different
things.

## Alerts

`screen_alert` gains both rules, pushing new same-day rows on its existing
~5-minute loop, with the kind named in the message.

**The reference needs one more number.** `screen_alert` does not recompute
forecasts; it re-folds the extreme `screen_reference.json` publishes. The
Guarded rule needs `min(remaining forecast)`, which the reference does not
carry. So `screen.py` publishes **`remaining`** per series/day, exactly as it
now publishes `realized` — a number it already has in hand.

The existing 90-minute `forecast_is_usable` guard then covers it for free: past
that, the alert falls back to Locked rows only, which need observations alone.
That is the same degradation path `dead` already has.

**A separate `screen_locked_alert_state.json`.** The fade state keys on bare
tickers, and a bracket can legitimately be a fade in the morning and Guarded by
evening — one shared file would let the first push permanently suppress the
second. A separate document also avoids a migration on a state file that only
just started working correctly (the `announced`/overflow fix of 2026-08-09).

## Failure modes

| What breaks | What happens |
|---|---|
| No observations for a city | Neither rule fires — both need a realized bound. |
| No forecast periods | Locked still fires; Guarded does not. |
| Reference older than 90 min | Alert falls back to Locked only, as it already does for `dead`. |
| `scan_locked.jsonl` unreadable | The section degrades to a caption; the fade table renders regardless. |
| Anything at all | `dead_candidate`, `forecast_candidate`, the fade log and `screen_score` are untouched. |

## Testing

- both rules, including the whole-°C slack boundary that produced the false
  Atlanta "dead"
- the one-threatened-edge property, per variable
- an unbounded tail routing to Locked and never to Guarded
- the YES band, both bounds, and an unquoted row
- **Phoenix as a regression case**: realized 93.0, remaining 96, `lo` 92, margin
  4.0, ask 39¢ → fires as Guarded
- the alert's message lines, its separate state document, and the stale-reference
  fallback to Locked only
- the view's columns, `Kind` labels and `—` fallbacks

## Honest status at ship

**This screen has no track record.** The fade side has 66 settled rows and a
measured −5.0% edge against the price paid. This one starts at zero, and its
Guarded half is a forecast bet in confident language. The log is what turns it
into knowledge. Treat the first weeks as observation rather than signal,
particularly on monsoon-season lows, where a 20% POP is exactly the risk the
market is charging for.
