"""ntfy event alerts fired from the scheduled run: the Morning Recap digest.

Pure message-builders + state I/O live here (unit-testable, no network/Streamlit);
`maybe_fire_events` orchestrates the once-per-day send. Kept cron-safe — no
Streamlit import at module top.

Storm Watch and Front Risk were retired 2026-08-07: the phone's alert budget now
goes to screen_alert, which pushes new same-day Screen rows.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import notify
import paths
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)

EVENT_STATE_PATH = os.path.join(os.path.dirname(__file__), "event_alert_state.json")
RECAP_HOUR, RECAP_MINUTE = 6, 30


def event_state_path(station: str = config.DEFAULT_STATION) -> str:
    """Per-station event-alert state file. KDFW keeps the module path (byte-
    identical, monkeypatchable); other stations namespace under data/<STATION>/."""
    return EVENT_STATE_PATH if station == config.DEFAULT_STATION \
        else paths.data_path("event_alert_state.json", station)


def _title(base: str, station: str) -> str:
    """Alert title. Default station keeps its familiar unprefixed title; other
    stations get a 'Austin: …' name prefix so multi-city pushes are unambiguous."""
    return base if station == config.DEFAULT_STATION \
        else f"{config.station(station).name}: {base}"


def load_state(path: str) -> dict:
    """Load a JSON alert-state dict, tolerating a missing/empty/corrupt file."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path: str, state: dict) -> None:
    with open(path, "w") as fh:
        json.dump(state, fh)


def recap_body(setup: dict | None, yesterday: dict | None) -> str:
    """Compact Morning Recap body: yesterday's scorecard (if settled) + today's
    setup. Empty string when `setup` is unavailable."""
    if not setup:
        return ""
    lines = []
    if yesterday:
        parts = []
        for var in ("high", "low"):
            g = yesterday.get(var)
            if not g:
                continue
            mark = ("Exact ✓" if g.get("exact")
                    else f"Miss {g['settled'] - g['model']:+g}")
            parts.append(f"{var.capitalize()} {g['settled']:g} "
                         f"(model {g['model']:g}, {mark})")
        if parts:
            lines.append("Yesterday: " + "; ".join(parts))
    lo = setup.get("low") or {}
    hi = setup.get("high") or {}
    lo_v = lo.get("observed")
    if lo_v is None:
        lo_v = lo.get("consensus")
    status = "Locked" if lo.get("locked") else "Developing"
    today = f"Today: Low ~{lo_v:.0f} ({status})" if lo_v is not None else "Today:"
    hi_v = hi.get("consensus")
    if hi_v is not None:
        today += f", High ~{hi_v:.0f}"
    lines.append(today)
    return "\n".join(lines)


def _build_recap_body(snap: dict) -> str:
    """Assemble the Morning Recap body from yesterday's scorecard + today's setup,
    mirroring app.load_recap. Best-effort — returns "" on any failure."""
    try:
        from datetime import date
        import forecast_log
        import recap
        import settlements
        bet_rows = None
        try:
            import bet_history
            bet_rows = bet_history.fetch_rows(bet_history.BETS_START)
        except Exception:
            bet_rows = None
        yesterday = recap.yesterday_scorecard(
            date.today(), settlements.as_map("cli"),
            forecast_log.load(), bet_rows=bet_rows)
        return recap_body(recap.today_setup(snap), yesterday)
    except Exception:
        return ""


def maybe_fire_events(snap: dict, now: datetime,
                      station: str = config.DEFAULT_STATION) -> None:
    """Fire the Morning Recap once per day. Best-effort: a failure logs and
    never blocks the surrounding scheduled run."""
    state_path = event_state_path(station)
    state = load_state(state_path)
    dirty = False

    def _send(key, day, title, body):
        nonlocal dirty
        if not day or not body or state.get(key) == day:
            return
        if notify.send_ntfy(title, body):
            state[key] = day
            dirty = True
            print(f"Event alert sent: {key}")
        else:
            print(f"Event alert: send_ntfy False for {key}")

    try:
        local = now.astimezone(_TZ)
        if (local.hour, local.minute) >= (RECAP_HOUR, RECAP_MINUTE):
            _send("recap", local.date().isoformat(), _title("Morning Recap", station),
                  _build_recap_body(snap))
    except Exception as e:
        print(f"Event alert skipped (recap): {e}")

    if dirty:
        try:
            save_state(state_path, state)
        except Exception as e:
            print(f"Event alert state save failed: {e}")
