# Model accuracy benchmark — 2026-06-30 (≈ day 15 of live trading)

**Baseline snapshot.** First formal accuracy review, ~2 weeks into live trading.
Raw exports are the six `.csv` files in this folder (pulled from the live
dashboard's self-scoring + backtest panels). Live trading record at this point:
**19 wins / 3 losses (86%).**

**Overall grade: B+**

> This is the reference point. Every future review copies its dashboard CSVs into
> `docs/benchmarks/<date>/`, re-runs the same analysis, and compares the headline
> numbers below to see whether the model is improving. Keep the metric definitions
> and table shapes identical so snapshots diff cleanly.

---

## Headline numbers to track over time

| Metric | 2026-06-30 baseline | Direction wanted |
|---|---|---|
| Same-day HIGH — within ±1°F | **100%** (9d) | hold ≥ 90% |
| Same-day HIGH — exact bin | 56% (9d) | hold / up |
| Same-day LOW — within ±1°F | **67%** (9d) ⚠️ bug-depressed | recover toward 90% |
| Same-day LOW — exact bin | 44% (9d) | up |
| Day-ahead HIGH — within ±1°F | **12%** (8d) ⚠️ warm bias | up off the floor |
| Day-ahead LOW — within ±1°F | 38% (8d) | up |
| Live self-scoring HIGH — Brier | 0.751 (17d) | down |
| Live self-scoring LOW — Brier | 0.935 (17d) ⚠️ bug-inflated | down toward ~0.75 |
| Market-vs-model HIGH — model MAE / market MAE | 1.1 / 1.1 (15d) | model ≤ market |
| Market-vs-model LOW — model MAE / market MAE | 1.41 / 1.03 (15d) ⚠️ bug-inflated | model ≤ market |
| High-confidence calibration (pred ≥0.85 → observed) | ~0.96 both vars | stay on the diagonal |

Brier here is the multi-category sum-of-squared-errors form (0 best, 2 worst).

---

## Raw metrics (as exported)

**Backtest** (61-day deterministic replay; flat spread, no same-day anchoring —
a *relative* A/B harness, not the live hit rate):

| var | days | exact | ±1°F | Brier | CRPS | MAE | MAE base | 50% cov | 80% cov |
|-----|------|-------|------|-------|------|-----|----------|---------|---------|
| high | 61 | 18% | 70% | 0.828 | 0.709 | 1.0 | 1.77 | 75% | 92% |
| low  | 61 | 41% | 74% | 0.702 | 0.635 | 1.06 | 1.02 | 74% | 89% |

**Exact-bin accuracy by lead (live):**

| lead | var | days | exact | ±1°F |
|------|-----|------|-------|------|
| same-day | high | 9 | 56% | 100% |
| same-day | low | 9 | 44% | 67% |
| day-ahead | high | 8 | 0% | 12% |
| day-ahead | low | 8 | 25% | 38% |

**Live self-scoring (17d):** high 29% / 59% / Brier 0.751 · low 35% / 53% / Brier 0.935

**Market vs model (15d):** high 1.1 / 1.1 / market-closer 60% · low 1.41 / 1.03 / market-closer 47%

**Reliability** (high & low `*_reliability.csv`): high-confidence end (predicted ≥0.85)
tracks the diagonal well on both variables; mid-range (0.3–0.7) wiggles are noise at n=17.

---

## Assessment

### Doing well
- **Same-day high is elite** — 100% within ±1°F (9/9), 56% exact. Strongest signal in the set; it's the lead you actually trade.
- **Calibration is trustworthy where you bet** — when the model says ≥85%, it's right ~96% (both vars). This is what the 19–3 record rests on.
- **Real structural edge on the high** — backtest MAE 1.0 vs 1.77 baseline (−43%); at parity with the Kalshi market (1.1 vs 1.1).
- **Low beats the market once the bug is removed** — stripping the 3 anchoring-artifact days drops low model MAE 1.55 → ~1.01 (vs market 1.13).

### Needs more time (small sample — don't over-read)
- **Day-ahead metrics (n=8)** look weak but the day-ahead bias-correction layer is still dormant (activates at 10 day-ahead days; we're at 8). Reassess once it engages.
- **Mid-range reliability** wiggles — need ~30–40 days to mean anything.
- **Live Brier / low numbers** still contaminated by pre-fix anchoring artifacts; re-baseline after clean post-fix days.

### Issues / fixes
1. **✅ Near-midnight same-day LOW anchoring bug — fixed 2026-06-30 (`5de6c8f`).** All 3 big low misses were captured 23:45–23:56 reading 81–82°F vs actual morning low 78–79 (model too *warm*, not storm crashes). Verify recovery over the next several same-day lows.
2. **Day-ahead HIGH warm bias** (~1–2°F warm → 0% exact / 12% within ±1). Target for the self-correction layer once it crosses the 10-day gate.
3. **Low lacks structural edge over baseline in backtest** (1.06 vs 1.02) — the one non-cosmetic modeling soft spot; separate from the anchoring bug.
4. **Intervals may be too wide** (backtest 50% cov → 74–75%, 80% cov → 89–92%; over-covering = under-confident). Candidate for modest sigma tightening, but only after more live days.

### What would move the grade to A−/A
1. 5+ clean post-fix days showing same-day-low within-±1 recover toward the high's 100%.
2. Day-ahead bias layer activating and pulling day-ahead within-±1 off the floor.
3. 30+ days holding the high-confidence calibration that already exists.
