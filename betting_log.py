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

import model

TZ = ZoneInfo(TIMEZONE)
_PATH = os.path.join(os.path.dirname(__file__), "betting_log.jsonl")

SLOTS = ["15:00", "15:30", "16:00", "16:30", "17:00"]
# The scheduler fires on a ~15-min cadence (GitHub cron at :07/:22/:37/:52 + the
# external 15-min trigger). Slots sit at :00/:30, so the nearest run to any slot is
# at most 7.5 min away. A ±8-min window therefore catches every slot regardless of
# the cron's phase — and under the :07/:22/:37/:52 fallback each slot gets TWO
# eligible runs (e.g. :00 is covered by both :52 and :07) instead of a single one
# hugging the boundary. The per-slot upsert makes redundant runs harmless.
SLOT_TOLERANCE_MIN = 8


def current_slot(now: datetime, slots=SLOTS, tol_min=SLOT_TOLERANCE_MIN) -> str | None:
    """Slot label if `now` is within `tol_min` minutes of a slot (local time), else None."""
    local = now.astimezone(TZ)
    for s in slots:
        hh, mm = (int(x) for x in s.split(":"))
        slot_dt = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if abs((local - slot_dt).total_seconds()) <= tol_min * 60:
            return s
    return None


def _parse(text: str) -> list[dict]:
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def _write(rows: list[dict], path: str) -> None:
    with open(path, "w") as fh:
        for rec in rows:
            fh.write(json.dumps(rec) + "\n")


def load(path: str | None = None) -> list[dict]:
    path = path or _PATH
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return _parse(fh.read())


def _key(rec: dict) -> tuple:
    return (rec["target_date"], rec["variable"], rec["capture_slot"])


def _top_bins(probabilities: dict, n: int = 5) -> list:
    items = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
    return [[label, round(p, 4)] for label, p in items[:n]]


def _row(day: str, variable: str, slot: str, cli_var: dict, hourly_var: dict,
         market_var: dict | None, flat_offset: float, captured: str) -> dict:
    obs = cli_var.get("observed_so_far")
    cont = cli_var.get("observed_continuous")
    live_gap = (cont - obs) if (obs is not None and cont is not None) else None
    rec = {
        "target_date": day,
        "variable": variable,
        "capture_slot": slot,
        "captured_at": captured,
        "cli_consensus": cli_var.get("consensus"),
        "hourly_consensus": (hourly_var or {}).get("consensus"),
        "flat_offset": flat_offset,
        "live_gap": live_gap,
        "observed_so_far": obs,
        "observed_continuous": cont,
        "peak_locked": cli_var.get("peak_locked"),
        "sigma_used": cli_var.get("sigma_used"),
        "model_bins": _top_bins(cli_var.get("probabilities") or {}),
    }
    if market_var:
        rec["market_ev"] = market_var.get("ev")
        rec["market_buckets"] = market_var.get("buckets")
    return rec


def record(cli_snapshot: dict, hourly_snapshot: dict, slot: str, calib: dict,
           path: str | None = None) -> None:
    """Upsert today's high & low betting-time rows for `slot`."""
    today = cli_snapshot.get("today")
    if not today:
        return
    day = today["day"]
    from datetime import date as _date
    day_d = _date.fromisoformat(day)
    captured = cli_snapshot.get("updated") or datetime.now(TZ).isoformat(timespec="seconds")
    market_today = (cli_snapshot.get("market") or {}).get("today", {})
    hourly_today = (hourly_snapshot or {}).get("today", {})

    new_recs = []
    for variable in ("high", "low"):
        cli_var = today.get(variable)
        if not cli_var or not cli_var.get("probabilities"):
            continue
        flat_offset, _std = model._offset_bucket(
            calib.get("settlement_offset"), variable, day_d, calib)
        new_recs.append(_row(day, variable, slot, cli_var,
                             hourly_today.get(variable), market_today.get(variable),
                             flat_offset, captured))

    target = path or _PATH
    rows = load(target)
    index = {_key(r): i for i, r in enumerate(rows)}
    for rec in new_recs:
        k = _key(rec)
        if k in index:
            rows[index[k]] = rec
        else:
            index[k] = len(rows)
            rows.append(rec)
    _write(rows, target)


def capture_if_slot(cli_snapshot: dict, hourly_snapshot: dict, calib: dict,
                    now: datetime | None = None) -> str | None:
    """If `now` falls in a betting slot, record the snapshot and return the slot."""
    now = now or datetime.now(TZ)
    slot = current_slot(now)
    if slot is None:
        return None
    record(cli_snapshot, hourly_snapshot, slot, calib)
    return slot


def main() -> None:
    """Standalone capture (dry-run / manual). The scheduler uses capture_if_slot
    with the snapshot it already built."""
    import calibration
    from datetime import date
    from sources import kalshi
    calib = calibration.get(refresh=True)
    off = (calib or {}).get("settlement_offset")
    cli = model.snapshot(calib, settle_offset=off, continuous_obs=True)
    hourly = model.snapshot(calib)
    try:
        today = date.fromisoformat(cli["today"]["day"])
        tomorrow = date.fromisoformat(cli["tomorrow"]["day"])
        cli["market"] = kalshi.implied_block(today, tomorrow)
    except Exception as e:
        print(f"market block skipped: {e}")
    slot = capture_if_slot(cli, hourly, calib)
    print(f"betting capture: slot={slot}")


if __name__ == "__main__":
    main()
