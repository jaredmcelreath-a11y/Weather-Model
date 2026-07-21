"""ntfy event alerts fired from the scheduled run: Storm Watch, Front Risk, and
the Morning Recap digest.

Pure message-builders + state I/O live here (unit-testable, no network/Streamlit);
`maybe_fire_events` orchestrates the once-per-day sends. Kept cron-safe — no
Streamlit import at module top.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import notify
import settlement
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)

EVENT_STATE_PATH = os.path.join(os.path.dirname(__file__), "event_alert_state.json")
RECAP_HOUR, RECAP_MINUTE = 6, 30


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


def storm_body(storm: dict) -> str:
    """Body for the Storm Watch Active alert."""
    sigma = storm.get("sigma") or 0.0
    up = storm.get("upstream") or {}
    if up.get("active"):
        return (f"SVR warning {up.get('county')} Co ({up.get('direction')}) · "
                f"low downside ±{sigma:g}°F")
    return f"Convective storms on the approach · low downside ±{sigma:g}°F"


def front_body(low: dict) -> str:
    """Body for the Front Risk alert."""
    fg = low.get("front_guard") or {}
    proj = fg.get("projection")
    if proj is None:
        proj = low.get("consensus")
    return f"Front may undercut tonight's low · projection ≈{proj:g}°F"


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
    today = f"Today: Low ~{lo_v:g} ({status})" if lo_v is not None else "Today:"
    hi_v = hi.get("consensus")
    if hi_v is not None:
        today += f", High ~{hi_v:g}"
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


def maybe_fire_events(snap: dict, now: datetime) -> None:
    """Fire the storm/front/recap alerts, each once per day. Best-effort per
    alert (one failing never blocks another) and overall."""
    state = load_state(EVENT_STATE_PATH)
    dirty = False
    try:
        cday = settlement.climate_day_of(now).isoformat()
    except Exception:
        cday = None

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
        storm = snap.get("storm") or {}
        if storm.get("level") == "active":
            _send("storm", cday, "Storm Watch Active", storm_body(storm))
    except Exception as e:
        print(f"Event alert skipped (storm): {e}")

    try:
        low = (snap.get("today") or {}).get("low") or {}
        if low.get("front_widened"):
            _send("front", cday, "Front Risk", front_body(low))
    except Exception as e:
        print(f"Event alert skipped (front): {e}")

    try:
        local = now.astimezone(_TZ)
        if (local.hour, local.minute) >= (RECAP_HOUR, RECAP_MINUTE):
            _send("recap", local.date().isoformat(), "Morning Recap",
                  _build_recap_body(snap))
    except Exception as e:
        print(f"Event alert skipped (recap): {e}")

    if dirty:
        try:
            save_state(EVENT_STATE_PATH, state)
        except Exception as e:
            print(f"Event alert state save failed: {e}")
