# SW storm coverage + morning low betting slots — design

**Date:** 2026-07-17
**Status:** Approved, ready for implementation plan

Two independent, small features from the deep-dive worklist, bundled into one
spec because both are low-risk config/data additions with no shared code.

---

## Feature 1 — SW/W storm coverage for the convective low floor

### Problem
`convective.py`'s upstream trigger (`_upstream_triggered`) fires the full
`CONVECTIVE_SIGMA` downside floor on today's low when a Severe Thunderstorm
Warning intersects `CONVECTIVE_UPSTREAM_UGC`. That set currently covers only the
**N/NW** approach counties plus the two metro counties. Storms that approach
KDFW from the **SW/W/S** — which in the warm season typically track NE toward and
over the metroplex — do not arm the trigger, so the model can print false high
confidence on the low while a SW storm complex bears down.

### Design
Pure data widening, no logic change. Extend `CONVECTIVE_UPSTREAM_UGC` in
`config.py` with the SW/W/S approach counties. `_upstream_triggered` already
returns `True` on any SVR warning intersecting the set and commands the full
`CONVECTIVE_SIGMA`, which matches the approved "same as NW" treatment (SW storms
move NE toward the metro, so they warrant the same insurance).

**Counties to add** (TXC + 3-digit county FIPS; verify each code against
api.weather.gov / NWS UGC during implementation):

| County    | UGC      | Position rel. to KDFW      |
|-----------|----------|----------------------------|
| Johnson   | `TXC251` | immediately S/SW of Tarrant |
| Hood      | `TXC221` | SW                          |
| Somervell | `TXC425` | SW                          |
| Erath     | `TXC143` | far SW (NE-tracking approach) |
| Ellis     | `TXC139` | S of Dallas                 |

**Deliberately excluded (YAGNI):** Hill `TXC217` and Bosque `TXC035` (far south).
Left out initially to avoid over-firing on storms too far south to reach KDFW
before midnight; add later only if we observe misses.

Update the `config.py` comment block from "N/NW approach" to "N/NW/SW approach"
so the rationale stays accurate.

### Motion-awareness — explicitly out of scope
A motion-aware gate (fire on SW warnings only when storm motion vectors toward
KDFW) was considered and rejected for this iteration: the alerts feed does not
reliably carry a usable motion vector, and the existing NW logic is already
motion-agnostic. Keep the two footprints consistent.

### Test
Extend `tests/test_convective.py`: add a fixture with an active SVR warning whose
`geocode.UGC` contains a SW county (e.g. `TXC221` Hood) and assert
`convective_sigma()` returns the full `CONVECTIVE_SIGMA`. Existing NW-warning and
no-warning tests must still pass unchanged.

---

## Feature 2 — morning betting slots for the low

### Problem
`betting_log.py` captures the model-vs-market snapshot only at afternoon slots
(`15:00–17:00` CDT), and `record()` logs **both** high and low at every slot. By
the afternoon, today's low bottomed out near the sunrise trough hours earlier, so
the afternoon low rows are settled and useless for edge measurement. There is no
capture at a betting-relevant time for the low.

### Design
Add early-morning slots that capture the low as it bottoms out near the sunrise
trough — the mirror of the high's afternoon peak-capture — and make each slot
declare which variable it captures, so no meaningless rows are written.

**New morning slots:** `05:00, 05:30, 06:00, 06:30, 07:00` CDT (5 slots, matching
the high's 5-slot pattern).

**Per-slot variable mapping (the one real design decision):**
- Morning slots (`05:00–07:00`) → capture **low only**
- Afternoon slots (`15:00–17:00`) → capture **high only**

Rationale: a morning "high" row is a ~10-hour day-ahead forecast, not a
betting-time high; an afternoon "low" row is already settled. Both are noise for
edge analysis. The symmetric mapping keeps `betting_log.jsonl` clean.

**Implementation shape:** replace the flat `SLOTS` list with a slot→variables
mapping (e.g. an ordered dict `{"05:00": ("low",), ..., "15:00": ("high",), ...}`
or a `SLOT_VARS` dict alongside `SLOTS`). `current_slot()` still returns the slot
label. `record()` iterates only the variable(s) the matched slot declares instead
of the hard-coded `("high", "low")`. `_key` is unchanged
`(target_date, variable, capture_slot)` — no collision between the morning-low and
afternoon-high rows.

**Behavior change to accept:** afternoon slots stop writing the settled-low rows
they currently write. `edge_report.py` groups by `(slot, variable, subset)`, so
existing high analysis is unaffected and the morning-low rows form a new low
cohort automatically. No historical rows are deleted; only future capture changes.

### No scheduler change required
The GitHub Action / external cron already fire every ~15 min around the clock
(`.github/workflows/log.yml` cron `7,22,37,52 * * * *`, plus the external
cron-job.org trigger), and `scheduled_log.py` already builds the Kalshi market
block and calls `betting_log.capture_if_slot()` whenever `current_slot()` is
non-None. Adding morning slots to the mapping makes them capture automatically,
market block included.

### Seasonal caveat — follow-up, not built now
The low trough shifts with sunrise (~6:25 summer → ~7:20 winter local), more than
the mid-afternoon high does. The fixed `05:00–07:00` window centers on summer
troughs. Seasonal window tuning (or sunrise-anchored morning slots) is recorded as
a follow-up, not built in this iteration.

### Test
Extend `tests/test_betting_log.py`:
- A `now` inside a morning slot records a **low** row and does **not** record a
  high row.
- A `now` inside an afternoon slot records a **high** row and no low row.
- `current_slot()` returns the correct label for a morning time.
- Existing afternoon-high behavior/keys unchanged.

---

## Non-goals (both features)
- No change to how `CONVECTIVE_SIGMA` is scaled or to the POP point-trigger path.
- No new data sources, no motion vectors, no seasonal slot logic.
- No migration of existing `betting_log.jsonl` rows.
