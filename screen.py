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
    write_reference: Callable = None
    read_reference: Callable = None


def _real_fetch_forecast(url):
    return ((get_json(url, ttl=900) or {}).get("properties") or {}).get("periods") or []


def fetch_observations(station, start, end):
    """A station's readings over a window. Public because screen_alert reuses it
    on its 5-minute loop."""
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
        fetch_obs=fetch_observations,
        append_rows=lambda path, rows: scan_log.append_many(path, rows),
        station_for=lambda url: scan_cities.station_for(url),
        write_reference=lambda obj: scan_log.write_doc(scan_log.REFERENCE_PATH, obj),
        read_reference=lambda: scan_log.read_doc(scan_log.REFERENCE_PATH),
    )


def merge_reference(previous: dict, current: dict) -> dict:
    """This pass's entries, over the identity of cities it could not reach.

    A pass is all-or-nothing per city but the document is not: api.weather.gov
    trips a 60s host cooldown after one timeout, and the whole 40-series loop
    runs inside that window, so a single slow response used to publish a
    two-city reference over a good forty-city one. Everything downstream reads
    this document -- screen_alert skips a series with no timezone outright, and
    the page's Day column and the consensus board both go blank -- so a
    degraded pass must not be allowed to delete cities.

    Only `station` and `timezone` are carried: they identify a city rather than
    describe a moment, and neither changes between passes. The measurements
    (`days`, `realized`, `remaining`) are deliberately dropped, because a stale
    realized extreme is exactly what screen_alert fires dead-row pushes from --
    and both alert rules already degrade safely when they are absent."""
    out = dict(current or {})
    for series, info in ((previous or {}).get("cities") or {}).items():
        if series in out:
            continue                      # this pass reached it; its data wins
        tzname = (info or {}).get("timezone")
        if not tzname:
            continue                      # nothing worth carrying
        out[series] = {"station": (info or {}).get("station"),
                       "timezone": tzname, "days": {}}
    return out


def observed_readings(features, tzname, day):
    """[(timestamp, F)] for readings inside the LST climate day.

    The timestamp kept is the reading's OWN aware time, not the LST-shifted one
    used to pick the day: the drift anchor compares it against `now` and against
    forecast period times, which are both real instants.

    Public because screen_alert reuses it on its 5-minute loop."""
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
            out.append((stamp, temp))
    return out


def screen_pass(now: datetime, deps: Deps) -> dict:
    """Screen every mapped city's ladder once."""
    now_iso = now.isoformat().replace("+00:00", "Z")
    candidates, cities, errors = [], 0, 0
    locked = []
    reference: dict = {}
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
        # Hoisted out of the day loop so the reference carries the station even
        # on a day that is not in progress: screen_alert fetches its own
        # observations from it and cannot afford to resolve stations itself.
        try:
            station = deps.station_for(resolved.get("stations_url"))
        except Exception as e:            # noqa: BLE001 - degrade to forecast only
            print(f"[screen] {series}: station lookup skipped ({e})")
            station = None
        reference[series] = {"station": station, "timezone": tzname, "days": {}}

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
            readings = []
            if in_progress:
                try:
                    features = deps.fetch_obs(station, start, now) if station else []
                    readings = observed_readings(features, tzname, day)
                except Exception as e:    # noqa: BLE001 - degrade to forecast
                    print(f"[screen] {series}: observations skipped ({e})")
            realized = [temp for _, temp in readings]

            extremes = screen_forecast.daily_extremes(periods, day, tzname)
            # The UNFOLDED extreme: screen_alert re-folds it against its own,
            # fresher observations, so folding here would bake in this pass's.
            reference[series]["days"][day.isoformat()] = extremes.get(variable)
            forecast = screen_forecast.fold_realized(
                extremes.get(variable), realized, variable)
            # Context for the human, not a screening input: a gap is only as
            # good as the forecast it is measured from, and convection is when
            # that forecast is least reliable. Free — the same payload.
            storm = screen_forecast.storm_chance(
                periods, day, tzname, variable, now)
            # Likewise free, and likewise never a screening input: how wrong the
            # forecast is against the station right now, applied to the forecast
            # HALF of the reference and re-folded. Shifting `forecast` directly
            # would move a realized extreme that has already happened — a high
            # that peaked at 95 does not become 92 because the forecast is
            # running 3° hot now.
            drift = screen_forecast.forecast_drift(periods, readings, now) \
                if in_progress else None
            drift_ref = None
            if drift is not None and extremes.get(variable) is not None:
                drift_ref = screen_forecast.fold_realized(
                    extremes[variable] + drift, realized, variable)
            for r in day_rows:
                hit = screen_rules.forecast_candidate(r, forecast, now_iso)
                if hit:
                    hit["storm"] = storm
                    hit["drift"] = drift
                    hit["drift_ref"] = drift_ref
                    candidates.append(hit)

            if not in_progress:
                continue
            bound = screen_rules.realized_extreme(realized, variable)
            # Published so city_consensus can fold its models against the same
            # realized extreme Ref is folded with, without refetching 20
            # cities' observations. Only for a day in progress -- tomorrow has
            # nothing realized, and an entry of None would read as "measured,
            # and it is nothing".
            reference[series].setdefault("realized", {})[day.isoformat()] = bound
            for r in day_rows:
                hit = screen_rules.dead_candidate(r, bound, now_iso)
                if hit:
                    hit["storm"] = storm
                    # A dead row's Ref is the realized bound, not a forecast.
                    hit["drift"] = hit["drift_ref"] = None
                    candidates.append(hit)

            # The YES side. `remaining` is published as well as used, because
            # screen_alert cannot recompute forecasts and the guarded rule needs
            # the hours still ahead -- exactly like `realized` above.
            remaining = screen_forecast.remaining_extreme(
                periods, day, tzname, variable, now)
            reference[series].setdefault("remaining", {})[day.isoformat()] = remaining
            for r in day_rows:
                # Locked first: it is the half that claims certainty, and
                # "cannot lose" is strictly more useful than "the forecast is
                # holding it".
                hit = screen_rules.locked_candidate(r, bound, now_iso)
                if hit is None:
                    hit = screen_rules.guarded_candidate(r, bound, remaining,
                                                         now_iso)
                if hit:
                    hit["storm"] = storm
                    locked.append(hit)

    written = deps.append_rows(scan_log.CANDIDATES_PATH, candidates)
    written_locked = deps.append_rows(scan_log.LOCKED_PATH, locked)
    # Best-effort and last: the candidate log is the product, and a contents-API
    # failure here must not cost the pass its rows.
    if deps.write_reference:
        try:
            previous = deps.read_reference() if deps.read_reference else {}
        except Exception as e:            # noqa: BLE001 - an unreadable previous
            print(f"[screen] previous reference unreadable ({e})")   # is not fatal
            previous = {}
        published = merge_reference(previous, reference)
        carried = len(published) - len(reference)
        if carried:
            print(f"[screen] reference: carried {carried} city entries forward")
        try:
            deps.write_reference({"generated": now_iso, "cities": published})
        except Exception as e:            # noqa: BLE001
            print(f"[screen] reference publish failed ({e})")
    return {"candidates": written or 0, "cities": cities, "errors": errors,
            "locked": written_locked or 0}


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
