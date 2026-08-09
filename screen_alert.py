"""Fast alert loop: push when a bracket settling TODAY newly appears on the
Screen table.

Runs every ~5 minutes, so it must stay cheap. It does NOT recompute the NWS
forecast: screen.py publishes screen_reference.json every 30 minutes and this
re-folds that extreme against its own fresh observations. Prices come straight
from Kalshi, which is what actually moves a row into the table.

Read-only against scan_candidates.jsonl — nothing here writes the candidate log,
so screen_score's measurement record stays comparable. Never imports
screen_view, which imports Streamlit and cannot run in a cron.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import scan_cities
import scan_log
import screen
import screen_forecast
import screen_rules

# Three missed 30-minute passes. Past this the forecast half of the reference is
# too old to call a gap news, so only the dead screen — which needs observations
# alone — may fire.
STALE_AFTER_MIN = 90


def reference_age_minutes(reference: dict, now: datetime):
    """Minutes since the reference was published, or None when unreadable."""
    stamp = (reference or {}).get("generated")
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - when).total_seconds() / 60.0


def forecast_is_usable(reference: dict, now: datetime) -> bool:
    """Whether the soft (forecast-gap) screen may fire on this reference."""
    age = reference_age_minutes(reference, now)
    return age is not None and age <= STALE_AFTER_MIN


# The climate day running right now in a city. Defined in screen_forecast so
# the Screen page can mark exactly the rows this loop is able to push.
in_progress_day = screen_forecast.in_progress_day


def day_window(day: date, tzname: str):
    """(start, end) in UTC of a city's LST climate day."""
    offset = screen_forecast.lst_offset_hours(tzname)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) \
        - timedelta(hours=offset)
    return start, start + timedelta(days=1)


def city_candidates(series: str, day: date, markets: list, realized: list,
                    now: datetime, forecast_extreme) -> list:
    """Alertable rows for one city's in-progress climate day.

    `forecast_extreme` is None when the reference is stale or missing, which
    disables the soft screen; the hard one needs only `realized`. Dead wins when
    both would fire — it is the half that claims certainty, and saying "already
    impossible" is strictly more useful than "far from the forecast".

    The price tested here IS the live price, unlike the page's, which compares a
    firing price up to hours old against a separately fetched quote."""
    now_iso = now.isoformat().replace("+00:00", "Z")
    variable = scan_log.variable_of_series(series)
    bound = screen_rules.realized_extreme(realized, variable)
    forecast = (screen_forecast.fold_realized(forecast_extreme, realized, variable)
                if forecast_extreme is not None else None)
    out = []
    for market in markets or []:
        row = scan_log.build_snapshot_row(market, series, now)
        if row is None:                   # unquoted; nothing to screen
            continue
        if screen_forecast.climate_day_of_ticker(row["ticker"]) != day:
            continue
        no_price = screen_rules.no_ask_of(market)
        if not screen_rules.within_band(no_price):
            continue
        hit = screen_rules.dead_candidate(row, bound, now_iso)
        if hit is None and forecast is not None:
            hit = screen_rules.forecast_candidate(row, forecast, now_iso)
        if hit is None:
            continue
        hit["no_price"] = no_price
        out.append(hit)
    return out


MAX_BODY_LINES = 10      # a notification longer than this is unreadable
STATE_KEEP_DAYS = 2
REQUEST_SPACING_S = 0.5  # the same Kalshi pacing the screen and scanner use


@dataclass
class Deps:
    read_reference: Callable
    read_state: Callable
    write_state: Callable
    list_markets: Callable
    fetch_obs: Callable
    notify: Callable
    sleep: Callable = time.sleep


def _real_deps() -> Deps:
    import notify as notify_mod
    from sources import kalshi
    return Deps(
        read_reference=lambda: scan_log.read_doc(scan_log.REFERENCE_PATH),
        read_state=lambda: scan_log.read_doc(scan_log.ALERT_STATE_PATH),
        write_state=lambda obj: scan_log.write_doc(scan_log.ALERT_STATE_PATH, obj),
        list_markets=lambda series: kalshi.list_series_markets(series, status="open"),
        fetch_obs=screen.fetch_observations,
        notify=notify_mod.send_ntfy,
    )


def _day_key(ticker: str):
    day = screen_forecast.climate_day_of_ticker(ticker)
    return None if day is None else day.isoformat()


def unseen(candidates: list, state: dict) -> list:
    """Candidates whose ticker has not been pushed for its own climate day.

    Deduplicates within the pass too: one bracket can be reached twice if a
    series ever lists it twice, and two notifications for one row is a bug the
    phone would show."""
    out, seen = [], set()
    for c in candidates:
        ticker = c.get("ticker")
        key = _day_key(ticker or "")
        if not ticker or key is None or ticker in seen:
            continue
        if ticker in set(state.get(key) or []):
            continue
        seen.add(ticker)
        out.append(c)
    return out


def record(state: dict, candidates: list) -> dict:
    """Mark these tickers as pushed, under their own climate day."""
    out = dict(state)
    for c in candidates:
        key = _day_key(c.get("ticker") or "")
        if key is None:
            continue
        out[key] = sorted(set(out.get(key) or []) | {c["ticker"]})
    return out


def prune(state: dict, today: date, keep_days: int = STATE_KEEP_DAYS) -> dict:
    """Drop days older than `keep_days`, and any key that is not a date.

    Kalshi temperature markets close within ~30h of listing, so two days is
    ample and keeps the file from growing without bound."""
    cutoff = today - timedelta(days=keep_days)
    out = {}
    for key, tickers in (state or {}).items():
        try:
            when = date.fromisoformat(str(key))
        except ValueError:
            continue
        if when >= cutoff:
            out[key] = tickers
    return out


def alert_title(count: int) -> str:
    return f"{count} new screen row" + ("" if count == 1 else "s")


def _line(c: dict) -> str:
    city = scan_cities.city_name(c.get("series"))
    label = c.get("label") or c.get("ticker")
    price = c.get("no_price")
    shown = "—" if price is None else f"{round(float(price) * 100)}%"
    reference = c.get("forecast")
    if c.get("kind") == "dead":
        word = "max" if c.get("variable") == "high" else "min"
        tail = f"DEAD ({word} {reference:g} already)"
    else:
        tail = f"Ref {reference:g} ({c.get('gap'):g}° gap)"
    return f"{city} {c.get('variable')} {label} · NO {shown} · {tail}"


def announced(candidates: list, max_lines: int = MAX_BODY_LINES) -> list:
    """The candidates this push actually NAMES.

    State records exactly this list and never the whole batch. A row the body
    could not fit has not been announced, so marking it pushed would lose it for
    good -- the same failure as advancing state on a failed POST, and for the
    same reason. The remainder simply arrives with the next pass, `max_lines` at
    a time, which drains a batch of any size within a few minutes."""
    return candidates[:max_lines]


def alert_body(candidates: list, max_lines: int = MAX_BODY_LINES) -> str:
    named = announced(candidates, max_lines)
    lines = [_line(c) for c in named]
    extra = len(candidates) - len(named)
    if extra > 0:
        lines.append(f"…and {extra} more in the next push")
    return "\n".join(lines)


def check(now: datetime, deps: Deps) -> dict:
    """One pass: price every mapped city's same-day ladder and push what is new."""
    reference = deps.read_reference() or {}
    usable = forecast_is_usable(reference, now)
    if not usable:
        age = reference_age_minutes(reference, now)
        print(f"[screen_alert] reference age {age}min — dead rows only"
              if age is not None else "[screen_alert] no reference — dead rows only")
    state = deps.read_state() or {}
    found, cities = [], 0
    for series, info in (reference.get("cities") or {}).items():
        tzname, station = info.get("timezone"), info.get("station")
        if not tzname:
            continue
        day = in_progress_day(now, tzname)
        try:
            markets = deps.list_markets(series)
            deps.sleep(REQUEST_SPACING_S)
        except Exception as e:            # noqa: BLE001 - one city must not
            print(f"[screen_alert] {series}: markets skipped ({e})")   # cost the rest
            continue
        cities += 1
        readings = []
        if station:
            start, _ = day_window(day, tzname)
            try:
                features = deps.fetch_obs(station, start, now)
                readings = screen.observed_readings(features, tzname, day)
            except Exception as e:        # noqa: BLE001 - degrade to forecast
                print(f"[screen_alert] {series}: observations skipped ({e})")
        extreme = (info.get("days") or {}).get(day.isoformat()) if usable else None
        found.extend(city_candidates(series, day, markets,
                                     [t for _, t in readings], now, extreme))
    fresh = unseen(found, state)
    if not fresh:
        return {"cities": cities, "found": len(found), "new": 0, "pushed": 0}
    named = announced(fresh)
    pushed = 0
    if deps.notify(alert_title(len(named)), alert_body(fresh)):
        # Only after a delivered push, and only what the body NAMED: advancing
        # state on a failed POST — or on a row the notification never mentioned
        # — loses the row for good, and this is the alert's entire purpose.
        deps.write_state(prune(record(state, named), now.date()))
        pushed = len(named)
    else:
        print("[screen_alert] send_ntfy False — state not advanced")
    return {"cities": cities, "found": len(found), "new": len(fresh),
            "pushed": pushed}


def main(argv: list, deps: Deps = None, now: datetime = None) -> int:
    if (argv[0] if argv else "") == "check":
        deps = deps or _real_deps()
        print(f"[screen_alert] {check(now or datetime.now(timezone.utc), deps)}")
        return 0
    print("usage: screen_alert.py check")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
