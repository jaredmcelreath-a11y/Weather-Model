"""Evidence script: can MOS (lav/nbs) be skill-weighted for day-ahead from the
Open-Meteo *archive*? Answer (2026-07-17): NO — the archive measures the NWP
systems at near-analysis lead, so a day-ahead MOS forecast can't be fairly
compared. This script reproduces that finding; it does NOT feed production
(calibration._system_extremes deliberately excludes MOS — see the module note
there and docs/benchmarks/2026-07-17-mos-weighting/ASSESSMENT.md).

It builds the with-MOS system set itself (merging iem_mos.historical_extremes
into the NWP-only _system_extremes) so it stands alone regardless of what
production folds.

Run: PYTHONPATH=. .venv/bin/python scripts/validate_mos_weighting.py
"""
from datetime import date, timedelta

import calibration
from config import CALIBRATION_WINDOW_DAYS
from sources import iem_mos, station_history

end = date.today() - timedelta(days=1)
start = end - timedelta(days=CALIBRATION_WINDOW_DAYS)

nwp = calibration._system_extremes(start, end)        # NWP-only (production shape)
mos = iem_mos.historical_extremes(start, end)         # day-ahead MOS extremes
actual = station_history.fetch_actual(start, end)     # {day: (high, low)}

# with-MOS = NWP systems + mos_lav/mos_nbs merged per day.
ext = {d: dict(sy) for d, sy in nwp.items()}
for d, sy in mos.items():
    for label, (hi, lo) in sy.items():
        if hi is not None:
            ext.setdefault(d, {})[label] = {"high": hi, "low": lo}

all_sys = sorted({s for day in ext.values() for s in day})
no_mos = [s for s in all_sys if not s.startswith("mos_")]
print(f"window {start}..{end}  days={len(actual)}")
print(f"systems_with_mos={all_sys}")

print("\nStandalone archive MAE / bias per system (reveals the lead mismatch):")
for var, idx in (("high", 0), ("low", 1)):
    print(f"  [{var}]")
    for s in all_sys:
        errs = [ext[d][s][var] - actual[d][idx]
                for d in ext if d in actual and s in ext[d]]
        if errs:
            mae = sum(abs(e) for e in errs) / len(errs)
            bias = sum(errs) / len(errs)
            print(f"    {s:20s} MAE={mae:.3f}  bias={bias:+.2f}  n={len(errs)}")

results = {}
for name, systems in (("with-MOS", all_sys), ("no-MOS", no_mos)):
    sub = {day: {s: v for s, v in sy.items() if s in systems}
           for day, sy in ext.items()}
    w = calibration._system_weights(sub, actual, systems)
    print(f"\n=== {name} ===")
    for var in ("high", "low"):
        gate = calibration._weights_beat_equal(sub, actual, systems, var)
        wmap = w[var] if gate else {s: 1.0 / len(systems) for s in systems}
        mae = calibration._consensus_mae(sub, actual, systems, var, wmap)
        mos_w = {s: round(wmap[s], 3) for s in systems if s.startswith("mos_")}
        results[(name, var)] = mae
        print(f"  {var}: gate={'PASS' if gate else 'fail'}  "
              f"consensus_MAE={mae:.3f}  mos_weights={mos_w}")

print("\n=== consensus MAE delta (with-MOS minus no-MOS; negative = improvement) ===")
for var in ("high", "low"):
    print(f"  {var}: {results[('with-MOS', var)] - results[('no-MOS', var)]:+.3f}")
