# Season readiness: bin range, tail abstain, boundary edges

**Date:** 2026-07-17
**Status:** design approved, ready for implementation plan
**Roadmap:** audit item #5 (season readiness), partially — `_LOW_WINDOW` deferred to its own spec

## Problem

The model's explicit bin range is `BIN_LOW = 60` .. `BIN_HIGH = 110` (`config.py:34-35`), with
open-ended `"<= 60"` and `">= 110"` tails. `model.bin_temp` maps each tail label to the *config
constant* rather than the number in the label:

```python
def bin_temp(label: str) -> int:
    if label.startswith("<="):
        return BIN_LOW      # 60
    if label.startswith(">="):
        return BIN_HIGH     # 110
    return int(label)
```

Because the `"<= 60"` tail resolves to exactly 60, `prob_at_most(probs, 59)` finds no bin at or
below 59 and returns **0** — not "unknown", but a confident zero. Verified against the real
functions:

```
probs = {'<= 60': 0.97, '61': 0.02, '62': 0.01}   # September front day, low ~55
P(low <= 59)                        -> 0
Kalshi "59 or below" (less, cap=60) -> 0
Kalshi "between 54-55"              -> 0
```

The same holds at the hot end: `P(high >= 111)` is 0 whenever mass sits in the `">= 110"` tail.

### Why this is a bug, not a rounding artifact

Nothing downstream guards the zero. `market_view.py:1041` computes `edge_no = (1 - p) - na`, so a
near-certain YES bucket priced at 15¢ NO yields `edge_no = 0.85` → a `BUY NO +85` signal that tops
the Top-3 ranking and gets sized by Kelly. The model would bet real money *against* a near-certain
outcome, with maximum confidence.

### Why it is not merely a winter concern

Eleven years of DFW dailies (4,018 days, 2015–2025, IEM `daily.json`, `TX_ASOS`):

| Condition | Days | Share |
|---|---|---|
| min <= 60 | 2,050 | 51% of all days |
| min <= 40 | 719 | 18% |
| min <= 20 | 34 | 0.8% |
| max >= 110 | 3 | 0.07% |
| max >= 112 | 0 | 0% |

Sample extremes: min **−2°F** (Feb 2021), max **110°F**. DFW all-time records: about −8°F and 113°F.

**More than half of all days** have a low inside the broken tail. The bug is dormant only because
the model has run solely in summer. The `Jul 17 – Sep 30` window shows the near-term exposure:
7 of 380 days had a min <= 60 (earliest **Sept 9**), and 3 days hit >= 110 (Aug 25–26 2023,
Sep 8 2023) — so the hot tail is reachable *this summer*.

## Scope

In scope:

1. Widen the explicit bin range to cover DFW's real climate.
2. Make `bin_temp` self-describing so logged rows survive the range change.
3. Abstain (`None`) instead of returning a false 0 when a query cuts inside an open tail.
4. Derive `edge_report.is_boundary`'s edges from config instead of hardcoding 60–120.

Out of scope (deferred to its own spec): `_LOW_WINDOW = (0, 9)` and evening-front source coverage.
It is a different subsystem (source coverage), it is display-only (`covers_extreme` reaches only
`per_source_extremes`, the transparency panel — `model.py:235`'s call sits inside the `not is_today`
branch, where day-ahead sources always supply hours 0–9 and never abstain), and the model's front
handling already comes from the shipped front guard (a30ce35).

## Approach

Chosen: **widen + self-describing `bin_temp` + abstain guard.**

Rejected alternatives:

- **One-time log migration** (rewrite `"<= 60"` → `"60"` on the data branch). The mass is ~0 so it
  would work, but it rewrites real history to work around a function that should have parsed its
  input, leaves `bin_temp` coupled to config for the *next* range change, and needs a force-push to
  the data branch.
- **Dynamic range from consensus ± k·sigma.** Makes `bin_labels()` stateful, changes signatures
  across `model`/`backtest`/`scoring`/`calibration`, and makes logged rows non-comparable across
  days — which breaks the exact-bin accuracy and calibration work that depends on a stable label
  set. Widening costs ~3.8ms; that is cheaper than all of this.

### Bin range: −10 to 115

Clears both all-time records with margin, so the tails hold negligible probability on every real
day and the abstain guard should never fire in practice.

Measured cost (40 ensemble members, `model._bin_probabilities`, 50-call warmup, best of 5×400):

| Range | Labels | ms/call |
|---|---|---|
| 60..110 (today) | 51 | 2.51 |
| 0..115 | 116 | 6.04 |
| **−10..115 (chosen)** | **126** | **6.32** |
| −10..120 | 131 | 6.63 |

~3.8ms over today, called a handful of times per render. Non-issue.

## Change set

**`config.py`** — `BIN_LOW = -10`, `BIN_HIGH = 115`. Update the comment: the range brackets DFW's
climate (records −8 / 113), not just the currently listed market.

**`model.py: bin_temp`** — parse the number out of the label (`"<= 60"` → 60, `">= 110"` → 110,
`"90"` → 90) instead of returning the config constant. This is the keystone of the design: it makes
every logged row mean what it meant when it was written, so no migration exists to get wrong, and it
deletes the hidden coupling in which a label's meaning silently depends on a mutable constant —
which is precisely the bug.

**`model.py: prob_at_least` / `prob_at_most`** — return `None` when the query cuts inside an open
tail. Tail edges are derived **from the probs dict itself** (the `<=` / `>=` labels present), not
from config, so a legacy row is judged by its own range:

```python
def _tail_edges(probs):
    """(low_edge, high_edge) from the dict's own tail labels; None where absent."""
    lo = hi = None
    for k in probs:
        if k.startswith("<="):
            lo = bin_temp(k)
        elif k.startswith(">="):
            hi = bin_temp(k)
    return lo, hi
```

- `prob_at_most(probs, t)` → `None` iff a low tail exists and `t < lo`.
- `prob_at_least(probs, t)` → `None` iff a high tail exists and `t > hi`.

Answerable cases that must keep working unchanged: `t` exactly on a tail edge (that is the whole
tail mass); `prob_at_least(t)` for `t <= lo` (1.0); `prob_at_most(t)` for `t >= hi` (1.0); and any
dict with no tail labels (a closed set, as several tests build) — nothing there is unanswerable,
because mass outside a closed set is genuinely zero.

**Propagation** — `prob_greater_than`, `prob_less_than`, `prob_for_contract`, and `prob_for_strike`
return `None` if a leg is `None`. For `between`, `None` if either leg is.

## Data flow: where `None` goes

`None` reaches exactly five consumers.

| Consumer | Behavior |
|---|---|
| `market_view.py:1037` main table | `if p is None` at the **top** of the loop → row with `—`, no signal, `continue` before picks/holds |
| `market_view.py:1093` open positions | Already `try/except` → `—`; make it explicit rather than rest on an exception |
| `kelly.best_side` | Return `None` when `p is None` — one guard covers `:1208`, `:1235`, `:1247` |
| `backtest.contract_points:76` | Skip `None` before the range check |
| `market_view.py:675` `prob_table` | No change; verify by test that it never yields `None` |

The main-table guard **must** come first in the loop: both `edge_no = (1 - p) - na` and the holds
loop's `("NO", 1 - p, na)` raise `TypeError` on `None`.

`kelly.best_side` returning `None` fits its existing contract ("None if neither side has an edge") —
an unpriceable contract simply is not sizable.

`backtest.py:76`'s `if not (0.01 <= p <= 0.99)` raises `TypeError: '<=' not supported between
instances of 'float' and 'NoneType'` (verified) — hence the explicit skip.

`prob_table` needs no guard because its thresholds are `bin_temp` of the dict's *own* labels, so they
land exactly on tail edges and never cut inside one. Pinned by test rather than assumed.

## Collateral changes

**`scoring.py:128`** — `within1` moves from `LABELS.index` distance to `bin_temp` distance:

```python
within1 = abs(bin_temp(peak_label) - bin_temp(actual_label)) <= 1
```

Equivalent for interior bins and for tails (`LABELS.index("<= 60")=0, index("61")=1` → 1;
`bin_temp` → 60, 61 → 1), but it cannot `ValueError` on a legacy tail label absent from the new
`LABELS`.

**`edge_report.py:31`** — derive the even-edge ladder from config, normalizing the start to an even
degree (Kalshi's 2°F buckets sit on even edges):

```python
start = BIN_LOW if BIN_LOW % 2 == 0 else BIN_LOW + 1
edges = [e + 0.5 for e in range(start, BIN_HIGH + 1, 2)]
```

**`settlement.py: bin_for_temp`** — logic unchanged; it now simply emits `"<= -10"` / `">= 115"`.

## Backward compatibility

The live data branch's `forecast_log.jsonl` (164 rows, 2026-06-16 → 2026-07-18) was measured:

- No row peaks in a tail bin → `scoring`'s `LABELS.index` cannot `ValueError` on existing history.
- Largest tail mass in any row: **0.55%**.

With `bin_temp` parsing, legacy rows keep their original meaning and no migration is required. New
rows carry 126 keys instead of 51, mostly zeros — an accepted cost of the stable-label-set design.

## Error handling

The abstain guard is a **backstop, not a feature**. With −10..115 it should never fire on real DFW
weather. If it fires, a contract exists outside our climate range, and abstaining is both correct
and visible (`—`) rather than a silent 0 that manufactures an 85-point phantom edge.

## Testing

Primary regression (the bug that started this):

- `P(low <= 55)` on a September-front distribution is a real number, not 0, and the corresponding
  Kalshi bucket prices sanely.
- A near-certain YES bucket outside the *old* range no longer produces a `BUY NO` signal.

Unit:

- `bin_temp` parses legacy `"<= 60"` → 60 and new `"<= -10"` → −10.
- Guard returns `None` for `prob_at_most(legacy_row, 59)` and `prob_at_least(probs, 116)`.
- Guard does **not** fire for closed dicts without tails, nor for queries landing on a tail edge.
- `best_side(None, ...)` is `None`.
- `contract_points` skips `None` without `TypeError`.
- `scoring`'s `within1` matches the old `LABELS.index` result on interior bins, and survives a
  legacy tail label.
- `is_boundary` derives edges from config; still `True` at 96.5 / 97.0 and `False` at 95.4 / 97.6
  (existing `tests/test_edge_report.py:34` assertions must keep passing).
- `prob_table` never yields `None`.

Characterization (no-regression on today's behavior):

- A real summer day: consensus unchanged; per-bin probabilities shift by no more than the old tail
  mass (≤0.55% on the worst logged row), since that mass merely redistributes into now-explicit
  bins.

## Acceptance criteria

1. No regression against the baseline: `python3 -m pytest -q --continue-on-collection-errors` gives
   **325 passed** (plus the 4 `test_bet_view` failures and 3 collection errors that are the known
   local `cryptography`/`streamlit` env gaps, not regressions — see the local-test-env notes).
2. `P(low <= 55)` on a front-day distribution returns a real probability, not 0.
3. No out-of-range contract can generate an edge signal, a Top-3 pick, a safe-hold, or a Kelly size.
4. Legacy `forecast_log` rows score identically before and after the change.
5. A summer day's consensus is unchanged.
