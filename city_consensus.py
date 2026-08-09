"""A free 5-model consensus temperature for every screened city.

The Screen page measures every gap from the NWS point forecast alone. This is
the independent second opinion: the same five deterministic models the KDFW
model already trusts, equal-weighted, with their spread as the honest statement
of how much they agree.

DISPLAY ONLY. Nothing here gates a flag, blends into Ref, or is imported by
screen_alert -- so screen_score's settled track record stays comparable across
this change.

Equal weight, deliberately. Skill weights at KDFW came from months of
self-scoring AT THAT STATION; nothing equivalent exists for Denver or Miami, and
inventing one would repeat the 2026-07-17 season-readiness bug where a number
derived from no data printed 0% and produced a live "BUY NO +85". The log this
module writes is how that can change on evidence instead of taste.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import scan_cities
import scan_log
import screen_forecast
from sources import open_meteo_cities

CONSENSUS_PATH = "city_consensus.json"
LOG_PATH = "city_consensus.jsonl"

# Below this many contributing models there is no consensus worth printing: two
# models agreeing is not agreement, and the spread of two is a range.
MIN_MODELS = 3

# Past this the published document is too old to show. The globals refresh every
# 6 hours and HRRR hourly, so a document this stale means the Action has been
# failing, not that the models have not moved.
STALE_AFTER_HOURS = 6

_PREFIX = "temperature_2m_"


def _lst_date(unix_ts, offset_hours: int) -> date:
    """The fixed-LST calendar date this instant falls on."""
    return (datetime.fromtimestamp(int(unix_ts), timezone.utc)
            + timedelta(hours=offset_hours)).date()


def series_extreme(times: list, temps: list, day: date,
                   offset_hours: int) -> dict:
    """{'high': f, 'low': f} for one model over one LST climate day.

    Mirrors screen_forecast.daily_extremes, which does the same reduction for
    NWS periods -- same day boundary, same Nones-when-absent contract."""
    values = [float(t) for stamp, t in zip(times or [], temps or [])
              if t is not None and _lst_date(stamp, offset_hours) == day]
    if not values:
        return {"high": None, "low": None}
    return {"high": max(values), "low": min(values)}


def model_extremes(hourly: dict, day: date, offset_hours: int) -> dict:
    """{model: {'high': f, 'low': f}} for every model with data on `day`.

    A model whose series is entirely null on this day is ABSENT rather than
    present with Nones -- routine for HRRR, which does not reach tomorrow."""
    times = (hourly or {}).get("time") or []
    out = {}
    for key, temps in (hourly or {}).items():
        if not key.startswith(_PREFIX):
            continue
        got = series_extreme(times, temps, day, offset_hours)
        if got["high"] is not None:
            out[key[len(_PREFIX):]] = got
    return out


def consensus(values: list):
    """{'value','spread','n'} over these model extremes, or None.

    Plain mean and full range. Rounded to a tenth because the inputs are
    tenths and a consensus printed to more places than its members would claim
    precision the models do not have."""
    numbers = [float(v) for v in (values or []) if v is not None]
    if len(numbers) < MIN_MODELS:
        return None
    return {"value": round(sum(numbers) / len(numbers), 1),
            "spread": round(max(numbers) - min(numbers), 1),
            "n": len(numbers)}


# ---- Building the published document ---------------------------------------

def cities_from_reference(reference: dict) -> list:
    """One entry per CITY from the reference's 40 series, sorted by code.

    The reference is keyed by series (two per city, one per variable); the
    consensus is per city, because a city has one coordinate and one fetch.
    A series with no timezone is one screen.py could not resolve and
    screen_alert already skips -- so it is skipped identically here."""
    cities = {}
    for series, info in (reference.get("cities") or {}).items():
        tzname = (info or {}).get("timezone")
        code = scan_cities.city_key(series)
        point = scan_cities.point_for(series)
        if not tzname or code is None or point is None:
            continue
        variable = scan_log.variable_of_series(series)
        entry = cities.setdefault(code, {
            "code": code, "name": scan_cities.name_of(code) or code,
            "lat": point[0], "lon": point[1], "timezone": tzname,
            "series": {}, "nws": {}, "realized": {},
        })
        entry["series"][variable] = series
        for day, value in (info.get("days") or {}).items():
            entry["nws"].setdefault(day, {})[variable] = value
        for day, value in (info.get("realized") or {}).items():
            entry["realized"].setdefault(day, {})[variable] = value
    return [cities[k] for k in sorted(cities)]


def target_days(now: datetime, tzname: str) -> list:
    """The climate day running now in this city, and the one after it.

    Exactly the two days Kalshi lists temperature markets for."""
    today = screen_forecast.in_progress_day(now, tzname)
    return [today, today + timedelta(days=1)]


def _entry(nws, cons, realized, variable: str) -> dict:
    """One variable's published block: both forms, plus the model detail.

    Unfolded is what a forecast SAID and is what the log scores. Folded is what
    can still be true given what has already happened, and is the only form
    comparable with the page's Ref."""
    value = None if cons is None else cons["value"]
    realized_list = [] if realized is None else [realized]
    return {
        "nws": nws,
        "nws_folded": screen_forecast.fold_realized(nws, realized_list, variable),
        "cons": value,
        "cons_folded": screen_forecast.fold_realized(value, realized_list,
                                                     variable),
        "spread": None if cons is None else cons["spread"],
        "n": 0 if cons is None else cons["n"],
    }


def build(reference: dict, raw: list, cities: list, now: datetime) -> dict:
    """The published document: every city, both days, both variables."""
    out = {}
    for city, payload in zip(cities, raw or []):
        offset = screen_forecast.lst_offset_hours(city["timezone"])
        days = {}
        for day in target_days(now, city["timezone"]):
            key = day.isoformat()
            extremes = model_extremes((payload or {}).get("hourly") or {},
                                      day, offset)
            block = {}
            for variable in ("high", "low"):
                values = [e[variable] for e in extremes.values()]
                cons = consensus(values)
                entry = _entry((city["nws"].get(key) or {}).get(variable),
                               cons,
                               (city["realized"].get(key) or {}).get(variable),
                               variable)
                entry["models"] = {m: e[variable] for m, e in extremes.items()}
                block[variable] = entry
            days[key] = block
        out[city["code"]] = {"name": city["name"], "timezone": city["timezone"],
                             "days": days}
    return {"generated": now.isoformat().replace("+00:00", "Z"), "cities": out}


# ---- The hourly forecast log -----------------------------------------------

def should_log(now: datetime) -> bool:
    """Whether this pass writes a log row.

    Once an hour, not once a pass: 20 cities x 2 variables x 2 days is 80 rows,
    and append_many rewrites the whole daily partition on every append. Hourly
    is already finer than the models update -- 6 hours for the globals, 1 for
    HRRR. Dispatches land at :00 and :30, so this picks exactly one of them.

    In Python rather than a bash minute-test so it can be tested; scan.yml has
    the shell version of this pattern for settlement and it is untestable."""
    return now.minute < 30


def log_rows(doc: dict, now: datetime) -> list:
    """One row per city / day / variable that has a consensus.

    UNFOLDED values only: a scorer grades what the forecast said, and folding
    mixes in what had already happened. A variable with no consensus is omitted
    rather than logged as Nones -- there is nothing to score, and the empty
    rows would dilute any later hit rate."""
    stamp = now.isoformat().replace("+00:00", "Z")
    rows = []
    for code, city in (doc.get("cities") or {}).items():
        for day, block in (city.get("days") or {}).items():
            for variable, entry in (block or {}).items():
                if (entry or {}).get("cons") is None:
                    continue
                rows.append({"ts": stamp, "city": code, "day": day,
                             "variable": variable, "nws": entry.get("nws"),
                             "cons": entry["cons"], "spread": entry.get("spread"),
                             "n": entry.get("n"), "models": entry.get("models")})
    return rows


@dataclass
class Deps:
    read_reference: Callable
    fetch: Callable
    write_doc: Callable
    append_rows: Callable


def _real_deps() -> Deps:
    return Deps(
        read_reference=lambda: scan_log.read_doc(scan_log.REFERENCE_PATH),
        fetch=lambda coords: open_meteo_cities.fetch(coords),
        write_doc=lambda path, obj: scan_log.write_doc(path, obj),
        append_rows=lambda path, rows: scan_log.append_many(path, rows),
    )


def run(now: datetime, deps: Deps) -> dict:
    """One pass: fetch every city at once, publish the document."""
    reference = deps.read_reference() or {}
    cities = cities_from_reference(reference)
    if not cities:
        print("[city_consensus] no reference on the data branch — skipped")
        return {"cities": 0, "logged": 0}
    try:
        raw = deps.fetch([(c["lat"], c["lon"]) for c in cities])
    except Exception as e:                # noqa: BLE001 - a dead upstream must
        print(f"[city_consensus] fetch skipped ({e})")   # not fail the job
        return {"cities": 0, "logged": 0}
    doc = build(reference, raw, cities, now)
    deps.write_doc(CONSENSUS_PATH, doc)
    logged = 0
    if should_log(now):
        rows = log_rows(doc, now)
        if rows:
            logged = deps.append_rows(LOG_PATH, rows) or 0
    return {"cities": len(doc["cities"]), "logged": logged}


def main(argv: list, deps: Deps = None, now: datetime = None) -> int:
    if (argv[0] if argv else "") == "run":
        deps = deps or _real_deps()
        print(f"[city_consensus] {run(now or datetime.now(timezone.utc), deps)}")
        return 0
    print("usage: city_consensus.py run")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
