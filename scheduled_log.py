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

import alerts
import betting_log
import calibration
import config
import consensus_log
import forecast_log
import model
import paths
import settlement
import settlements
from sources import kalshi

def _record_settlements(station: str = config.DEFAULT_STATION) -> int:
    """Persist actual settlements for any settled forecast day — the one job
    that needs no calibration, so it runs even when the model logging is
    skipped. Best-effort: an archive hiccup just leaves days for the next run."""
    try:
        settlements.record(station=station)
    except Exception as e:
        print(f"settlement recording skipped: {e}")
    return len(settlements.load(station=station))


def _attach_market(cli_snap: dict, now: datetime,
                   station: str = config.DEFAULT_STATION) -> None:
    """Attach the live Kalshi market to `cli_snap`, in place.

    Always the today/tomorrow block. During the final climate hour also the
    still-open prior day, and on a close slot the raw ask ladder for the day that
    is closing. Every branch is best-effort — a market outage must never block
    the model logging around it.
    """
    try:
        today = date.fromisoformat(cli_snap["today"]["day"])
        tomorrow = date.fromisoformat(cli_snap["tomorrow"]["day"])
        cli_snap["market"] = kalshi.implied_block(today, tomorrow, station=station)
    except Exception as e:
        print(f"market block skipped: {e}")
        cli_snap["market"] = cli_snap.get("market") or {}

    prior = settlement.open_prior_day(now, station)
    if prior:
        block = {}
        for var in ("high", "low"):
            try:
                implied = kalshi.implied_forecast(var, prior, station=station)
            except Exception:
                implied = None
            if implied:
                block[var] = implied
        if block:
            cli_snap["market"]["yesterday"] = block

    slot = betting_log.current_slot(now)
    if slot in betting_log.ASK_SLOTS:
        target = betting_log.slot_target_day(slot, now, station)
        asks = {}
        for var in betting_log.SLOT_VARS.get(slot, ("high", "low")):
            try:
                rows = kalshi.ask_rows(var, target, station=station)
            except Exception:
                rows = None
            if rows:
                asks[var] = rows
        if asks:
            cli_snap["market_asks"] = asks


def _log_snapshots(calib: dict, off, station: str = config.DEFAULT_STATION) -> None:
    """The model-logging body of a scheduled run: CLI snapshot + market block +
    forecast/consensus logs + the slot-gated betting capture."""
    now = datetime.now(model.TZ)
    cli_snap = model.snapshot(calib, settle_offset=off, continuous_obs=True,
                              include_candidate=True, station=station)
    _attach_market(cli_snap, now, station)
    alerts.maybe_fire_events(cli_snap, now, station)
    forecast_log.record(cli_snap, basis="cli", station=station)
    consensus_log.record(cli_snap, basis="cli", station=station)
    # Betting-time capture: only when `now` falls in a betting slot. Best-effort:
    # an error here doesn't block the logging above. record() auto-routes by the
    # snapshot's station tag, so betting_log needs no station argument here.
    try:
        if betting_log.current_slot(now) is not None:
            hourly_snap = model.snapshot(calib, station=station)
            slot = betting_log.capture_if_slot(cli_snap, hourly_snap, calib, now=now)
            print(f"betting-time capture at slot {slot}")
    except Exception as e:
        print(f"betting capture skipped: {e}")


def _publish_det_models(station: str = config.DEFAULT_STATION) -> None:
    """Publish the deterministic-models forecast to the data branch from this
    un-throttled Action IP, so the live app can read it when api.open-meteo.com
    rate-limits its shared Streamlit Cloud IP. Best-effort — a miss just leaves
    the previous copy in place. KDFW writes the bare det_models.json; other
    stations write data/<STATION>/det_models.json."""
    try:
        from sources import open_meteo_models
        if station == config.DEFAULT_STATION:
            path = os.path.join(os.path.dirname(__file__), open_meteo_models.PUBLISHED_FILE)
        else:
            path = paths.data_path(open_meteo_models.PUBLISHED_FILE, station)
            os.makedirs(os.path.dirname(path), exist_ok=True)
        open_meteo_models.write_published(path, station=station)
        print(f"[{station}] published {open_meteo_models.PUBLISHED_FILE}")
    except Exception as e:
        print(f"[{station}] det_models publish skipped: {e}")


def run_station(code: str) -> None:
    """One scheduled run for a single station: calibration, det_models publish,
    model logging, and settlements — all routed to `code`.
    Raised errors propagate to `main`, which isolates each station."""
    calib = calibration.get(refresh=True, station=code)
    off = (calib or {}).get("settlement_offset")
    if off is None:
        # No calibration at all (recompute failed AND no cached copy — a >24h
        # sustained outage): the snapshot would be hourly-basis numbers, and
        # logging them as basis="cli" would silently poison the scoring cohort.
        # Skip ALL model logging this run; settlements need no calibration.
        print(f"[{code}] calibration unavailable — skipping model logging (settlements only)")
        s = _record_settlements(code)
        print(f"[{code}] settlements log holds {s} records")
        return
    print(f"[{code}] calibration: using copy computed {calib.get('computed', 'unknown')}")
    _publish_det_models(code)
    _log_snapshots(calib, off, code)
    s = _record_settlements(code)
    n = len(forecast_log.load(station=code))
    print(f"[{code}] logged cli snapshot; log now holds {n} records, {s} settlements")


def main() -> None:
    """Run every configured station, isolating failures so one station's outage
    never blocks the other."""
    for code in config.STATION_CODES:
        try:
            run_station(code)
        except Exception as e:
            print(f"[{code}] scheduled run failed: {e}")


if __name__ == "__main__":
    main()
