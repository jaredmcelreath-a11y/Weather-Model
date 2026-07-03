"""Betting-time forward log — a slot-keyed snapshot of the model + Kalshi market
at fixed afternoon clock times (15:00-17:00 CDT), so the model-vs-market edge and
the settlement-gap predictor can be measured at the moment bets are placed.

Separate from forecast_log.jsonl on purpose: forecast_log upserts on
(target_date, variable, lead_bucket) and would overwrite the same-day row every
run. This log keys on the capture slot, so each afternoon snapshot persists.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from config import TIMEZONE
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)
_PATH = os.path.join(os.path.dirname(__file__), "betting_log.jsonl")

SLOTS = ["15:00", "15:30", "16:00", "16:30", "17:00"]
SLOT_TOLERANCE_MIN = 7


def current_slot(now: datetime, slots=SLOTS, tol_min=SLOT_TOLERANCE_MIN) -> str | None:
    """Slot label if `now` is within `tol_min` minutes of a slot (local time), else None."""
    local = now.astimezone(TZ)
    for s in slots:
        hh, mm = (int(x) for x in s.split(":"))
        slot_dt = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if abs((local - slot_dt).total_seconds()) <= tol_min * 60:
            return s
    return None
