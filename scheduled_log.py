"""Record one forward-log snapshot.

Run on a schedule by GitHub Actions (see .github/workflows/log.yml) so the
self-scoring log keeps growing in the cloud even when no one is viewing the
dashboard and the host computer is off. It appends/upserts to the local
forecast_log.jsonl; the workflow restores that file from the `data` branch
beforehand and republishes it afterward, so the log persists across runs.

Deliberately does NOT set the FORECAST_LOG_GH_* env vars, so forecast_log works
the local file directly here (the dashboard, not this script, reads from GitHub).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

import betting_log
import calibration
import consensus_log
import forecast_log
import model
import settlement
import settlements
from sources import kalshi

STATE_PATH = os.path.join(os.path.dirname(__file__), "cli_alert_state.json")


def _record_settlements() -> int:
    """Persist actual settlements for any settled forecast day — the one job
    that needs no calibration, so it runs even when the model logging is
    skipped. Best-effort: an archive hiccup just leaves days for the next run."""
    try:
        settlements.record()
    except Exception as e:
        print(f"settlement recording skipped: {e}")
    return len(settlements.load(settlements._PATH))


def _attach_market(cli_snap: dict, now: datetime) -> None:
    """Attach the live Kalshi market to `cli_snap`, in place.

    Always the today/tomorrow block. During the final climate hour also the
    still-open prior day, and on a close slot the raw ask ladder for the day that
    is closing. Every branch is best-effort — a market outage must never block
    the model logging around it.
    """
    try:
        today = date.fromisoformat(cli_snap["today"]["day"])
        tomorrow = date.fromisoformat(cli_snap["tomorrow"]["day"])
        cli_snap["market"] = kalshi.implied_block(today, tomorrow)
    except Exception as e:
        print(f"market block skipped: {e}")
        cli_snap["market"] = cli_snap.get("market") or {}

    prior = settlement.open_prior_day(now)
    if prior:
        block = {}
        for var in ("high", "low"):
            try:
                implied = kalshi.implied_forecast(var, prior)
            except Exception:
                implied = None
            if implied:
                block[var] = implied
        if block:
            cli_snap["market"]["yesterday"] = block

    if betting_log.current_slot(now) in betting_log.CLOSE_SLOTS:
        closing = settlement.climate_day_of(now)
        asks = {}
        for var in ("high", "low"):
            try:
                rows = kalshi.ask_rows(var, closing)
            except Exception:
                rows = None
            if rows:
                asks[var] = rows
        if asks:
            cli_snap["market_asks"] = asks


def _log_snapshots(calib: dict, off) -> None:
    """The model-logging body of a scheduled run: CLI snapshot + market block +
    forecast/consensus logs + the slot-gated betting capture."""
    now = datetime.now(model.TZ)
    cli_snap = model.snapshot(calib, settle_offset=off, continuous_obs=True,
                              include_candidate=True)
    _attach_market(cli_snap, now)
    forecast_log.record(cli_snap, basis="cli")
    consensus_log.record(cli_snap, basis="cli")
    # Betting-time capture: only when `now` falls in a betting slot.
    # Best-effort: an error here doesn't block the logging above.
    try:
        if betting_log.current_slot(now) is not None:
            hourly_snap = model.snapshot(calib)
            slot = betting_log.capture_if_slot(cli_snap, hourly_snap, calib, now=now)
            print(f"betting-time capture at slot {slot}")
    except Exception as e:
        print(f"betting capture skipped: {e}")


def _maybe_alert_cli(now: datetime) -> None:
    """Send one ntfy push the first time today's CLIDFW report is seen.

    Fires from the 10-min Action so it works even when no one has the dashboard
    open. `STATE_PATH` (persisted on the data branch) records the last-alerted
    day so later runs stay quiet. Best-effort: any failure is logged and skipped.
    """
    try:
        import notify
        from sources import nws_cli
        cli = nws_cli.fetch_latest_cli(ttl=0)  # always fresh in the cron
        today = settlement.climate_day_of(now)
        if not cli or cli["report_date"] != today:
            return
        state = {}
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as fh:
                state = json.load(fh)
        if state.get("last_alerted_day") == today.isoformat():
            return
        msg = (f'High {cli["high_f"]:g}°F · Low {cli["low_f"]:g}°F'
               f' · issued {cli["issued"].strftime("%-I:%M %p")}')
        if notify.send_ntfy("Dallas Climate Report", msg):
            with open(STATE_PATH, "w") as fh:
                json.dump({"last_alerted_day": today.isoformat()}, fh)
            print(f"CLI alert sent for {today}")
    except Exception as e:
        print(f"CLI alert skipped: {e}")


def main() -> None:
    from sources.common import TZ
    _maybe_alert_cli(datetime.now(TZ))
    calib = calibration.get(refresh=True)
    off = (calib or {}).get("settlement_offset")
    if off is None:
        # No calibration at all (recompute failed AND no cached copy — a >24h
        # sustained outage): the snapshot would be hourly-basis numbers, and
        # logging them as basis="cli" would silently poison the scoring cohort.
        # Skip ALL model logging this run; settlements need no calibration.
        print("calibration unavailable — skipping model logging (settlements only)")
        s = _record_settlements()
        print(f"settlements log holds {s} records")
        return
    print(f"calibration: using copy computed {calib.get('computed', 'unknown')}")
    _log_snapshots(calib, off)
    s = _record_settlements()
    n = len(forecast_log.load(forecast_log._PATH))
    print(f"logged cli snapshot; log now holds {n} records, {s} settlements")


if __name__ == "__main__":
    main()
