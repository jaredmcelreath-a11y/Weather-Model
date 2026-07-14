"""Real-data confirmation of the LST settlement window on the discriminating
day found during verification (May 26 2026: CLI min 67 recorded 11:59 PM LST =
12:59 AM CDT May 27, which the old clock window dropped).

Recomputes day_high_low for May 26 under BOTH windows from the real 5-min ASOS
feed and checks the LST window matches the CLIDFW value (67) while the clock
window does not; a winter control day must be identical under both.

Run from the repo root:
  .venv/bin/python docs/benchmarks/2026-07-14/lst-window/replay_lst_window.py
"""
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import settlement
from config import TIMEZONE
from sources.station_history import _fetch_series

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "RESULTS.md")
TZ = ZoneInfo(TIMEZONE)
CLOCK_TZ = TZ  # the OLD window basis

# (day, expected CLI low from the CLIDFW product, kind)
CASES = [
    (date(2026, 5, 26), 67, "BOUNDARY (min recorded 12:59 AM CDT May 27)"),
    (date(2026, 1, 15), None, "WINTER CONTROL (LST == clock)"),
]


def _clock_bounds(day):
    start = datetime(day.year, day.month, day.day, tzinfo=CLOCK_TZ)
    return start, start + timedelta(days=1)


def _min_in(times, temps, start, end):
    vals = [v for t, v in zip(times, temps)
            if v is not None and start <= t.astimezone(TZ) < end]
    return settlement.round_half_up(min(vals)) if vals else None


def main():
    lines = ["# LST window replay — real 5-min ASOS obs", ""]
    all_ok = True
    for day, cli_low, kind in CASES:
        # Pull a two-day span so the post-midnight tail (the LST window's last
        # hour, 00:00-01:00 the next clock day) is available. The IEM ASOS
        # archive's day2 bound is EXCLUSIVE (empirically verified: day2=day+1
        # returns nothing past day 23:59), so request day+2 to actually include
        # the day+1 calendar day's readings.
        times, temps = _fetch_series(day, day + timedelta(days=2))
        lst_start, lst_end = settlement.local_day_bounds(day)
        clk_start, clk_end = _clock_bounds(day)
        lst_low = _min_in(times, temps, lst_start, lst_end)
        clk_low = _min_in(times, temps, clk_start, clk_end)
        lines.append(f"## {day} — {kind}")
        lines.append(f"- LST-window min:   {lst_low}")
        lines.append(f"- clock-window min: {clk_low}")
        if cli_low is not None:
            ok = (lst_low == cli_low and clk_low != cli_low)
            lines.append(f"- CLIDFW low: {cli_low} — "
                         f"{'PASS (LST matches, clock does not)' if ok else 'FAIL'}")
            all_ok = all_ok and ok
        else:
            ok = (lst_low == clk_low)
            lines.append(f"- control: {'PASS (identical)' if ok else 'FAIL (differ)'}")
            all_ok = all_ok and ok
        lines.append("")
    with open(OUT, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
