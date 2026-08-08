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

from datetime import date, datetime, timedelta, timezone

import scan_log
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


def in_progress_day(now: datetime, tzname: str) -> date:
    """The climate day running right now in this city.

    Fixed LST, not local time: the climate day ends at 01:00 local during
    daylight saving, so the local date is a day ahead for that hour."""
    offset = screen_forecast.lst_offset_hours(tzname)
    return (now.astimezone(timezone.utc) + timedelta(hours=offset)).date()


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
