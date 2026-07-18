# Model accuracy benchmark — 2026-07-18 (≈ day 33 of live trading)

**Second formal accuracy review**, ~2.5 weeks after the 2026-06-30 baseline.
Numbers captured from the live dashboard's Accuracy page (CLI/NWS-settlement
basis — what Kalshi resolves on). Raw exports are the `.csv` files in this folder.

**Overall grade: A−** (up from B+ on 2026-06-30)

> Compared table-for-table against `docs/benchmarks/2026-06-30/`. Metric
> definitions and table shapes kept identical so snapshots diff cleanly. The
> 2026-06-30 backtest was already on the CLI basis (its MAE-base of 1.77 matches
> CLI, not hourly), so the backtest rows are apples-to-apples.

---

## Headline numbers vs the 2026-06-30 baseline

| Metric | 2026-06-30 | 2026-07-18 | Direction |
|---|---|---|---|
| Live self-scoring HIGH — exact bin | 29% (17d) | **41%** (51d) | ✅ up |
| Live self-scoring HIGH — within ±1 | 59% (17d) | **67%** (51d) | ✅ up |
| Live self-scoring HIGH — Brier | 0.751 (17d) | **0.664** (51d) | ✅ down |
| Live self-scoring LOW — exact bin | 35% (17d) | **53%** (51d) | ✅ up |
| Live self-scoring LOW — within ±1 | 53% (17d) | **76%** (51d) | ✅ up |
| Live self-scoring LOW — Brier | 0.935 (17d) ⚠️bug | **0.664** (51d) | ✅✅ bug fixed |
| Day-ahead HIGH — within ±1 | 12% (8d) | **32%** (25d) | ✅ off the floor |
| Day-ahead HIGH — exact bin | 0% (8d) | **12%** (25d) | ✅ off the floor |
| Day-ahead LOW — within ±1 | 38% (8d) | **64%** (25d) | ✅ up |
| Market-vs-model HIGH — model/market MAE | 1.1 / 1.1 (15d) | 1.2 / 1.07 (47d) | ~parity |
| Market-vs-model LOW — model/market MAE | 1.41 / 1.03 (15d) ⚠️bug | **0.89 / 0.87** (47d) | ✅ competitive |
| Backtest HIGH — exact bin | 18% (61d) | **33%** (61d) | ✅ up |
| Backtest LOW — within ±1 | 74% (61d) | **85%** (61d) | ✅ up |
| Backtest LOW — MAE / base | 1.06 / 1.02 (61d) | 0.97 / 0.96 (61d) | ⚠️ still ≈ base |

Brier is the multi-category sum-of-squared-errors form (0 best, 2 worst).

---

## Raw metrics (as exported)

**Backtest** (61-day deterministic replay; flat spread, no same-day anchoring —
a *relative* A/B harness, not the live hit rate):

| var | days | exact | ±1°F | Brier | CRPS | MAE | MAE base | 50% cov | 80% cov |
|-----|------|-------|------|-------|------|-----|----------|---------|---------|
| high | 61 | 33% | 72% | 0.788 | 0.705 | 1.0 | 1.88 | 77% | 90% |
| low  | 61 | 34% | 85% | 0.755 | 0.651 | 0.97 | 0.96 | 70% | 92% |

**Live self-scoring (51d each):** high 41% / 67% / Brier 0.664 · low 53% / 76% / Brier 0.664

**Exact-bin accuracy by lead (live):**

| lead | var | days | exact | ±1°F |
|------|-----|------|-------|------|
| same-day | high | 0 | — | — |
| same-day | low | 0 | — | — |
| day-ahead | high | 25 | 12% | 32% |
| day-ahead | low | 25 | 24% | 64% |

The same-day rows are empty — the fixed 09:00 capture cohort (`3e735e7`) shipped
2026-07-17 13:01, missed that day's 9am window, and its first capture (2026-07-18
09:00) targets today, which hasn't settled yet. It populates from 2026-07-19 on.

**Market vs model (47d each):** high 1.2 / 1.07 / market-closer 51% · low 0.89 / 0.87 / market-closer 34%

**Calibration state:** high bias −0.9 / low bias −0.2°F · day-ahead σ 1.0/1.1°F ·
settle offset +0.78/−0.37°F · cooling +0.06°F · day-ahead corr +0.61/−0.45°F ·
window 46d. Active self-corrections: day-ahead high +0.6, day-ahead low −0.4,
same-day high σ=0.5, same-day low σ=1.2, day-ahead high σ=2.5, day-ahead low σ=1.6
(4 storm/front records excluded from the correction pool).

**Reliability:** both backtest and live curves hug the diagonal; the high-confidence
end (pred ≥0.85) still tracks well on both variables. (Exact per-bin values not
exported this round — read from the live charts.)

---

## Assessment

### Doing well — clear gains since baseline
- **Live self-scoring improved on every metric, at 3× the sample** (17→51 days).
  This is the honest graded-against-settlement number, so it carries the most weight.
- **The near-midnight LOW anchoring bug is fixed and confirmed** — live low Brier
  0.935 → 0.664, exact 35→53%, within-±1 53→76%. That was the worst number at baseline.
- **The day-ahead self-correction layer went from dormant to active** — it was stuck
  below its 10-day gate at baseline (8 days); now 25 days, applying +0.6/−0.4°F.
  Day-ahead within-±1 came off the floor (high 12→32%, low 38→64%). Baseline
  issue #2 *and* grade-mover #2 — done.
- **Market-vs-model now competitive on both variables** — model low MAE 1.41→0.89,
  matching the market (0.87), model the closer forecast 66% of the time. High is parity.
- **Backtest high sharpened** — exact 18→33%, Brier 0.828→0.788, structural edge
  held (MAE 1.0 vs base 1.88, −47%).

### Still open
- **Low has no structural edge over baseline in the backtest** (MAE 0.97 vs 0.96;
  backtest Brier actually crept up 0.702→0.755, exact 41→34%). Same soft spot flagged
  on 2026-06-30 — the one thing keeping this off a clean A.
- **Same-day cohort not yet visible** — the elite "same-day high 100% within ±1"
  metric has no row until the 09:00 cohort accumulates settled mornings (starts 7/19).
  Because only the single ~09:00 run lands in the ±8-min window, it builds slowly and
  can gap on days the scheduler drops that run.

### What would move the grade to A / A+
1. Same-day cohort accumulating 5+ settled mornings and confirming same-day-high
   within-±1 back near the old 100%.
2. Low backtest developing real edge over baseline (MAE gap opening below 0.96,
   Brier back under 0.70) — the last structural soft spot.
3. Holding the current live calibration (Brier 0.66/0.66) over another 30+ days.

### Grade-movers from the 2026-06-30 list
1. ~~Same-day-low within-±1 recovery~~ — obscured (cohort 1 day old); overall live
   low within-±1 did rise 53→76%.
2. ✅ Day-ahead bias layer active and pulling day-ahead within-±1 off the floor.
3. ✅ 30+ days holding high-confidence calibration (now 51 live days, Brier 0.66/0.66).

Two of three achieved → **B+ → A−**.
