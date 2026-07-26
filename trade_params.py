"""Trading parameter schema, defaults, coercion, and the market-hours window.

Pure — no IO, no network. `DEFAULT_PARAMS` is the single source of truth for
what a trade-state document may contain; `merge_params` normalizes a stored
document against it. Ships SAFE: kill switch engaged, shadow mode.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import config

# kill_switch True == ENGAGED == trading disabled.
# Ships DISENGAGED (False) so the trader is ACTIVE by default — but `mode`
# ships "shadow", and shadow places NO real orders (kalshi_orders.place_order
# no-ops unless mode == "live"). So the real-money guard is the SHADOW default,
# not the kill switch: a fresh/absent trade-state doc runs the shadow trader
# (logs decisions, zero real fills); placing real orders still requires an
# explicit, persisted mode="live".
DEFAULT_PARAMS: dict = {
    "kill_switch": False,
    "mode": "shadow",                     # "shadow" | "live" — shadow = no real orders
    "enabled_variables": ["high", "low"],
    "min_resolved": 0.70,                 # fraction 0-1
    "agreement_tol": 1.0,                 # °F
    "max_price": 0.94,                    # skip asks >= this
    "min_price": 0.10,                    # skip asks < this
    "kelly_fraction": 0.25,
    "per_market_cap": 0.50,               # dollars per bracket
    "max_open_per_variable": 1,
    "daily_loss_cap": -5.00,              # dollars; None disables
    "stop_loss": 0.20,                    # ask drop from entry_ask that exits
    "slippage_cap": 0.02,                 # dollars through the ask a buy may pay
    "market_open": "06:00",               # HH:MM local (config.TIMEZONE)
    "market_close": "20:00",
}

_BOOL = {"kill_switch"}
_FLOAT = {"min_resolved", "agreement_tol", "max_price", "min_price",
          "kelly_fraction", "per_market_cap", "stop_loss", "slippage_cap"}
_INT = {"max_open_per_variable"}


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def merge_params(stored: dict | None) -> dict:
    """DEFAULT_PARAMS overlaid with `stored`; unknown keys dropped, types coerced."""
    out = dict(DEFAULT_PARAMS)
    for k, v in (stored or {}).items():
        if k not in DEFAULT_PARAMS:
            continue
        if k in _BOOL:
            out[k] = _coerce_bool(v)
        elif k == "daily_loss_cap":
            out[k] = None if v is None else float(v)
        elif k in _FLOAT:
            out[k] = float(v)
        elif k in _INT:
            out[k] = int(v)
        else:
            out[k] = v
    return out


def _hhmm(s: str) -> time:
    hh, mm = (int(x) for x in str(s).split(":", 1))
    return time(hh, mm)


def within_market_window(now: datetime, params: dict) -> bool:
    """True when `now` (tz-aware) falls inside [market_open, market_close] in
    config.TIMEZONE."""
    local = now.astimezone(ZoneInfo(config.TIMEZONE))
    return _hhmm(params["market_open"]) <= local.time() <= _hhmm(params["market_close"])
