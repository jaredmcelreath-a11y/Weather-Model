"""Convective downside trigger for today's daily low.

The smooth gridded fields the model ingests cannot see a thunderstorm
downdraft, so on a storm day the model locks to the morning low and reports
false high confidence. This module decides, best-effort, how much downside
spread evening convection warrants before midnight — scaled by the remaining-
hours precip probability (POP) at KDFW, or pinned to the full floor by an active
severe-thunderstorm warning in the N/NW approach counties. model.py uses the
returned sigma to floor the low's spread.

POP, not CAPE, is the gate: CAPE measures latent instability that runs high on
storm-free summer afternoons, so arming on it spread the locked low downward
almost every hot day. POP is the model's actual expectation that storms fire.
"""

from __future__ import annotations

from datetime import date, datetime

from config import (CONVECTIVE_POP_FULL, CONVECTIVE_POP_MIN, CONVECTIVE_SIGMA,
                    CONVECTIVE_SIGMA_MIN, CONVECTIVE_UPSTREAM_UGC)
from sources import nws_alerts, open_meteo_models

UPSTREAM_UGC = frozenset(CONVECTIVE_UPSTREAM_UGC)
_SEVERE = "Severe Thunderstorm Warning"


def _point_triggered(pop, pop_min=CONVECTIVE_POP_MIN) -> bool:
    """True when the remaining-hours precip probability clears the arming
    threshold. CAPE is deliberately not a trigger (see module docstring)."""
    return pop is not None and pop >= pop_min


def _point_sigma(pop, pop_min=CONVECTIVE_POP_MIN, pop_full=CONVECTIVE_POP_FULL,
                 lo=CONVECTIVE_SIGMA_MIN, hi=CONVECTIVE_SIGMA) -> float:
    """Downside sigma the point POP warrants: 0 below the arming threshold,
    ramping linearly from `lo` at pop_min to the full `hi` at/above pop_full."""
    if not _point_triggered(pop, pop_min):
        return 0.0
    frac = min(1.0, (pop - pop_min) / max(pop_full - pop_min, 1e-9))
    return lo + frac * (hi - lo)


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


def convective_sigma(day: date, now: datetime) -> float:
    """One-sided downside sigma floor for today's low (0.0 = no convective risk).

    An active upstream severe-thunderstorm warning is direct evidence of storms
    on the approach, so it commands the full CONVECTIVE_SIGMA. Otherwise the floor
    scales with the point precip probability (see `_point_sigma`). Best-effort:
    each signal is guarded independently, and any data/network failure simply
    contributes no downside (never raises)."""
    try:
        if _upstream_triggered(nws_alerts.fetch_active()):
            return CONVECTIVE_SIGMA
    except Exception:
        pass
    try:
        pop, _cape = open_meteo_models.convective_window(day, now)
        return _point_sigma(pop)
    except Exception:
        return 0.0


def convective_risk(day: date, now: datetime) -> bool:
    """Back-compat boolean: True when any convective downside applies."""
    return convective_sigma(day, now) > 0.0
