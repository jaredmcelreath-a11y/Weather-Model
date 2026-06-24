"""Convective downside trigger for today's daily low.

The smooth gridded fields the model ingests cannot see a thunderstorm
downdraft, so on a storm day the model locks to the morning low and reports
false high confidence. This module decides, best-effort, whether evening
convection could still set a new lower minimum before midnight — from point
POP/CAPE at KDFW or an active severe-thunderstorm warning in the N/NW approach
counties. model.py uses the decision to floor the low's spread.
"""

from __future__ import annotations

from datetime import date, datetime

from config import (CONVECTIVE_CAPE_MIN, CONVECTIVE_POP_MIN,
                    CONVECTIVE_UPSTREAM_UGC)
from sources import nws_alerts, open_meteo_models

UPSTREAM_UGC = frozenset(CONVECTIVE_UPSTREAM_UGC)
_SEVERE = "Severe Thunderstorm Warning"


def _point_triggered(pop, cape, pop_min=CONVECTIVE_POP_MIN,
                     cape_min=CONVECTIVE_CAPE_MIN) -> bool:
    """True when remaining-hours POP or CAPE clears its arming threshold."""
    return ((pop is not None and pop >= pop_min)
            or (cape is not None and cape >= cape_min))


def _upstream_triggered(alerts: dict, zones=UPSTREAM_UGC) -> bool:
    """True when an active Severe Thunderstorm Warning intersects `zones`."""
    for f in (alerts or {}).get("features", []):
        props = f.get("properties", {}) or {}
        if props.get("event") != _SEVERE:
            continue
        ugc = (props.get("geocode", {}) or {}).get("UGC", []) or []
        if zones.intersection(ugc):
            return True
    return False


def risk_label(low_pred: dict) -> str | None:
    """Dashboard caption when the low's spread was convectively widened."""
    if (low_pred or {}).get("convective_widened"):
        return ("⚡ Convective risk — evening storms could set a new low; "
                "confidence on the low has been widened.")
    return None


def convective_risk(day: date, now: datetime) -> bool:
    """True if evening convection could push today's low lower before midnight.

    Best-effort: each signal is guarded independently, and any data/network
    failure contributes no risk (returns without raising). Point POP/CAPE OR an
    upstream severe-thunderstorm warning is sufficient."""
    try:
        pop, cape = open_meteo_models.convective_window(day, now)
        if _point_triggered(pop, cape):
            return True
    except Exception:
        pass
    try:
        if _upstream_triggered(nws_alerts.fetch_active()):
            return True
    except Exception:
        pass
    return False
