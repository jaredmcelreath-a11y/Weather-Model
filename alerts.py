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
