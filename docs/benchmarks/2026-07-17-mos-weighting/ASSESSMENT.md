# MOS/NBM skill-weighting — validation & decision (2026-07-17)

**Question:** Can we lower day-ahead consensus MAE by making IEM MOS (LAV + NBS)
skill-weighted systems, measured from the archive?

**Decision: HOLD the archive-based weighting.** Ship the plumbing (fetch,
`_sample_weights` routing, forward-logging MOS); weight MOS from the forward log
at matched lead in a follow-up. The archive cannot fairly weight MOS for
day-ahead.

Reproduce: `PYTHONPATH=. .venv/bin/python scripts/validate_mos_weighting.py`

## What the archive experiment showed

Window 2026-06-01..2026-07-16 (46 days), hourly-basis actuals.

Folding MOS into `_system_extremes` **regressed** the offline consensus MAE:

| var  | no-MOS MAE | with-MOS MAE | delta |
|------|-----------:|-------------:|------:|
| high | 0.941      | 1.034        | +0.092 |
| low  | 0.873      | 1.013        | +0.140 |

The walk-forward gate did **not** protect against this: the gate only tilts
weights *within* a fixed system set; it never questions whether MOS belongs in
the set. On the low the gate failed and shipped the equal-8-system baseline,
which is itself worse (0.873 → 1.013) because MOS is in the pool.

## Root cause — the archive is near-analysis for NWP, not day-ahead

Standalone per-system archive MAE (high):

| system            | MAE   | bias  |
|-------------------|------:|------:|
| det_gfs_hrrr      | 0.804 | −0.15 |
| det_gfs_seamless  | 0.804 | −0.15 |
| det_icon_seamless | 1.087 | −0.22 |
| **mos_nbs**       | 2.109 | −0.15 |
| **mos_lav**       | 2.174 | −0.43 |

**`det_gfs_hrrr` at 0.80°F day-ahead-high MAE is physically impossible** — HRRR is
an 18–48h model; a genuine day-ahead high can't be forecast to 0.8°F. The
Open-Meteo historical-forecast archive returns essentially a **best-analysis
(~0-hour) fit** per past day for the NWP models. MOS, by contrast, is measured at
a **true 24–38h day-ahead lead** (prior-day 12Z run), giving a realistic ~2.1°F
error. So the comparison is NWP-at-≈0h vs MOS-at-day-ahead — apples to oranges,
rigged against MOS. (The existing NWP skill-weighting inherits this: it is
calibrated on near-analysis fit and applied at all leads — a pre-existing
limitation, see the `calibration.py` scope note.)

## The hopeful signal

MOS's day-ahead **bias** is near-zero (mos_nbs high −0.15) exactly where the
deep-dive measured the *model's* day-ahead high at **+0.9°F warm**. MOS is a
well-centered day-ahead source; its value as a de-warming/centering input is
real. Its higher *scatter* (MAE) at day-ahead is expected and can only be judged
against NWP **at the same lead** — which the archive can't provide.

## Path forward

The only fair instrument is the **forward log at matched lead**: it captures every
source (now including MOS per-model, shipped here) at true live day-ahead lead.
Once ~3–4 weeks of MOS day-ahead days settle, build a forward-log-based day-ahead
system weight and revisit. Until then:

- **Shipped (harmless, correct foundation):** `iem_mos.historical_extremes`;
  `_sample_weights` routes `mos_*` to its own weight when one exists;
  `forecast_log` records MOS per-model.
- **Held:** folding MOS into `calibration._system_extremes` (would regress, per
  above).
- **Follow-up:** forward-log-based day-ahead weighting (own spec/plan).
