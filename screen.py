"""Multi-city mispriced-bracket screen.

Each firing, list the brackets worth a human's attention: priced richly but far
from the NWS forecast (soft), or already made impossible by realized temperature
(hard). Writes candidates to scan_candidates.jsonl; never trades them.

Read-only. Imports nothing from the trading modules.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import scan_cities
import scan_log
import screen_forecast
import screen_rules
from sources import kalshi
from sources.common import get_json

REQUEST_SPACING_S = 0.5     # same Kalshi pacing the scanner needs


@dataclass
class Deps:
    list_series: Callable
    list_markets: Callable
    resolve_point: Callable
    fetch_forecast: Callable
    fetch_obs: Callable
    append_rows: Callable
    station_for: Callable
    sleep: Callable = time.sleep


def _real_fetch_forecast(url):
    return ((get_json(url, ttl=900) or {}).get("properties") or {}).get("periods") or []


def _real_fetch_obs(station, start, end):
    url = f"https://api.weather.gov/stations/{station}/observations"
    params = {"start": start.isoformat().replace("+00:00", "Z"),
              "end": end.isoformat().replace("+00:00", "Z"), "limit": 500}
    return (get_json(url, params, ttl=300) or {}).get("features") or []


def _real_deps() -> Deps:
    return Deps(
        list_series=kalshi.list_weather_series,
        list_markets=kalshi.list_series_markets,
        resolve_point=lambda lat, lon: scan_cities.resolve(lat, lon),
        fetch_forecast=_real_fetch_forecast,
        fetch_obs=_real_fetch_obs,
        append_rows=lambda path, rows: scan_log.append_many(path, rows),
        station_for=lambda url: scan_cities.station_for(url),
    )


def _observed_temps_f(features, tzname, day):
    """Fahrenheit readings inside the LST climate day."""
    offset = timedelta(hours=screen_forecast.lst_offset_hours(tzname))
    out = []
    for f in features or []:
        props = f.get("properties") or {}
        temp = screen_rules.c_to_f((props.get("temperature") or {}).get("value"))
        if temp is None:
            continue
        try:
            stamp = datetime.fromisoformat(str(props.get("timestamp")))
        except (TypeError, ValueError):
            continue
        utc_offset = stamp.utcoffset()
        if utc_offset is None:
            continue
        lst = stamp - utc_offset + offset
        if lst.date() == day:
            out.append(temp)
    return out


def screen_pass(now: datetime, deps: Deps) -> dict:
    """Screen every mapped city's ladder once."""
    now_iso = now.isoformat().replace("+00:00", "Z")
    candidates, cities, errors = [], 0, 0
    for s in deps.list_series():
        series = s["ticker"]
        point = scan_cities.point_for(series)
        if point is None:
            continue                      # city not mapped; simply not screened
        try:
            markets = deps.list_markets(series, status="open")
            deps.sleep(REQUEST_SPACING_S)
            resolved = deps.resolve_point(*point)
            tzname = resolved.get("timezone")
            periods = deps.fetch_forecast(resolved.get("forecast_hourly"))
        except Exception as e:            # noqa: BLE001 - one city must not
            print(f"[screen] {series}: skipped ({e})")   # cost the others
            errors += 1
            continue
        if not tzname:
            errors += 1
            continue
        cities += 1

        rows = [r for r in (scan_log.build_snapshot_row(m, series, now)
                            for m in markets) if r is not None]
        # Group by the climate day each bracket settles on, so today's and
        # tomorrow's markets are each screened against their own day.
        by_day: dict = {}
        for r in rows:
            day = screen_forecast.climate_day_of_ticker(r["ticker"])
            if day is not None:
                by_day.setdefault(day, []).append(r)

        variable = scan_log.variable_of_series(series)
        for day, day_rows in by_day.items():
            # Observations come FIRST when the climate day is already running:
            # the remaining forecast periods no longer contain an extreme that
            # has already happened, so the forecast screen needs them too.
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) \
                - timedelta(hours=screen_forecast.lst_offset_hours(tzname))
            in_progress = start <= now < start + timedelta(days=1)
            realized = []
            if in_progress:
                try:
                    station = deps.station_for(resolved.get("stations_url"))
                    features = deps.fetch_obs(station, start, now) if station else []
                    realized = _observed_temps_f(features, tzname, day)
                except Exception as e:    # noqa: BLE001 - degrade to forecast
                    print(f"[screen] {series}: observations skipped ({e})")

            extremes = screen_forecast.daily_extremes(periods, day, tzname)
            forecast = screen_forecast.fold_realized(
                extremes.get(variable), realized, variable)
            for r in day_rows:
                hit = screen_rules.forecast_candidate(r, forecast, now_iso)
                if hit:
                    candidates.append(hit)

            if not in_progress:
                continue
            bound = screen_rules.realized_extreme(realized, variable)
            for r in day_rows:
                hit = screen_rules.dead_candidate(r, bound, now_iso)
                if hit:
                    candidates.append(hit)

    written = deps.append_rows(scan_log.CANDIDATES_PATH, candidates)
    return {"candidates": written or 0, "cities": cities, "errors": errors}


def main(argv: list, deps: Deps = None, now: datetime = None) -> int:
    deps = deps or _real_deps()
    now = now or datetime.now(timezone.utc)
    if (argv[0] if argv else "") == "run":
        print(f"[screen] {screen_pass(now, deps)}")
        return 0
    print("usage: screen.py run")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
