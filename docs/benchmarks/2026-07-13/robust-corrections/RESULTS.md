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
