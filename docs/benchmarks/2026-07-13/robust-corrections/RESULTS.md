# Storm-proof corrections — before/after on the live data-branch logs

Run date: 2026-07-13

| estimator | OLD (all-time mean/std) | NEW (windowed, flagged-out, median) |
|---|---|---|
| bias | {0: {'low': 0.33}, 24: {'high': 0.64}} | {24: {'high': 0.93, 'low': -0.46}} |
| sigma | {0: {'high': 0.57, 'low': 1.25}, 24: {'high': 1.65, 'low': 1.56}} | {0: {'high': 0.57, 'low': 1.25}, 24: {'high': 1.65, 'low': 1.56}} |
| sigma @ today=2026-08-15 | — | {24: {'high': 1.66, 'low': 1.57}, 0: {'high': 0.48, 'low': 0.16}} |

- Gate 1 (lead-0 low phantom correction gone): PASS (old: 0.33)
- Gate 2 (lead-24 high correction survives): PASS (old: 0.64, new: 0.93)
- Gate 3 (lead-0 low sigma self-heals by 2026-08-15): PASS (now: 1.25, aug: 0.16)

## Note: new day-ahead low correction

The NEW estimator also emits a lead-24 LOW correction of -0.46 that the OLD
mean-based path never did — it passed the median-SE gate legitimately, but it
was not one of the three validation gates, so it ships as a live behavior
change worth watching. It applies on the pure-forecast low path, where the
warm-night low correction can also apply — watch the dashboard's "Active
self-corrections" line for the first week for signs of double-counting.

Pool stats behind this correction (from forecast_log.jsonl / settlements.jsonl):
- n=20 median=-0.65 sd=1.56 se=0.437
