"""A/B replay of the front-aware locked low on real KDFW days.

Finds recent days whose daily minimum occurred in the EVENING (a front undercut
the morning low — the exact failure the guard targets) plus a calm dawn-low
control day, then replays predict_variable at several intraday times with the
guard ON (shipped config) and OFF (margin=inf → today's pre-guard behavior).

Success criteria (spec): on front days the guarded run shifts the locked low
toward the coming front while the baseline stays pinned to the morning min; on
the control day the two runs are identical.

Run from the repo root:  .venv/bin/python docs/benchmarks/2026-07-13/front-guard/replay_front_guard.py
"""
import math
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

import model
from config import TIMEZONE
from settlement import local_day_bounds
from sources import open_meteo_models, station_history
from sources.common import to_hourly
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)
OUT = os.path.join(os.path.dirname(__file__), "RESULTS.md")

SEARCH_START = date(2026, 1, 15)   # winter/spring: front season
SEARCH_END = date(2026, 5, 15)
NOW_HOURS = [10, 14, 18, 21]       # intraday replay times (local)


def _daily_min_hour(times, temps, day):
    """(min_temp, local hour of the min) for `day` from the hourly series."""
    start, end = local_day_bounds(day)
    best = None
    for t, v in zip(times, temps):
        t = t.astimezone(TZ)
        if start <= t < end and (best is None or v < best[0]):
            best = (v, t.hour + t.minute / 60.0)
    return best


def find_days():
    """(front_days, control_day): days whose min landed after 18:00 local, and
    one calm day whose min landed before 09:00."""
    times, temps = to_hourly(*station_history._fetch_series(SEARCH_START, SEARCH_END))
    fronts, control = [], None
    day = SEARCH_START
    while day <= SEARCH_END:
        got = _daily_min_hour(times, temps, day)
        if got:
            _, hr = got
            if hr >= 18:
                fronts.append(day)
            elif hr <= 9 and control is None:
                control = day
        day += timedelta(days=1)
    return fronts[-2:], control     # the two most recent front days


def replay(day, guard_on):
    """[(hour, consensus, sigma, front_widened, observed_min_so_far)] for `day`."""
    series = open_meteo_models.fetch_historical(day, day + timedelta(days=1))
    obs = {"obs": to_hourly(*station_history._fetch_series(day, day))}
    saved = model.FRONT_UNDERCUT_MARGIN
    model.FRONT_UNDERCUT_MARGIN = saved if guard_on else math.inf
    rows = []
    try:
        for h in NOW_HOURS:
            now = datetime(day.year, day.month, day.day, h, tzinfo=TZ)
            out = model.predict_variable(series, obs, day, "low", now, None)
            if out:
                rows.append((h, out["consensus"], out["sigma_used"],
                             out["front_widened"], out["observed_so_far"]))
    finally:
        model.FRONT_UNDERCUT_MARGIN = saved
    return rows


def main():
    fronts, control = find_days()
    days = [(d, "FRONT") for d in fronts] + ([(control, "CONTROL")] if control else [])
    actual = station_history.fetch_actual_cli(min(d for d, _ in days),
                                              max(d for d, _ in days))
    lines = ["# Front-guard replay — guarded (ON) vs today's behavior (OFF)", ""]
    for day, kind in days:
        settled = actual.get(day, (None, None))[1]
        lines.append(f"## {day} ({kind}) — settled CLI low: {settled}")
        lines.append("| now | consensus ON | consensus OFF | sigma ON | sigma OFF | flag |")
        lines.append("|---|---|---|---|---|---|")
        on, off = replay(day, True), replay(day, False)
        for (h, c1, s1, fw, _obs), (_, c0, s0, _, _) in zip(on, off):
            lines.append(f"| {h}:00 | {c1} | {c0} | {s1} | {s0} | {fw} |")
        lines.append("")
    with open(OUT, "w") as fh:
        fh.write("\n".join(lines))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
