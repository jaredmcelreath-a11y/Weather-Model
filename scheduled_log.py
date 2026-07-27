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

STATE_PATH = os.path.join(os.path.dirname(__file__), "cli_alert_state.json")
RESOLVED_STATE_PATH = os.path.join(os.path.dirname(__file__), "resolved_alert_state.json")
# Fire the "Locked In" push at 80% (was 70%). Obs-replay calibration (2026-07-27)
# showed 70% Resolved is only ~52% exact-bin (coin flip); the bracket doesn't
# become reliable (~83%) until ~80%, where the market has usually not yet fully
# priced it — the intended entry window.
RESOLVED_ALERT_PCT = 80


def _state_path(default_path: str, basename: str, station: str) -> str:
    """Per-station alert-state file. The default station keeps the module-level
    path (byte-identical, monkeypatchable by tests); others namespace under
    data/<STATION>/ so log.yml persists them alongside the rest."""
    return default_path if station == config.DEFAULT_STATION \
        else paths.data_path(basename, station)


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

    if betting_log.current_slot(now) in betting_log.CLOSE_SLOTS:
        closing = settlement.climate_day_of(now, station)
        asks = {}
        for var in ("high", "low"):
            try:
                rows = kalshi.ask_rows(var, closing, station=station)
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
    _maybe_alert_resolved(cli_snap, now, station)
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


def _maybe_alert_cli(now: datetime, station: str = config.DEFAULT_STATION) -> None:
    """Send one ntfy push the first time today's CLI report is seen.

    Fires from the 10-min Action so it works even when no one has the dashboard
    open. The per-station state file (persisted on the data branch) records the
    last-alerted day so later runs stay quiet. Best-effort: any failure logs and
    skips."""
    try:
        import notify
        from sources import nws_cli
        name = config.station(station).name
        state_path = _state_path(STATE_PATH, "cli_alert_state.json", station)
        cli = nws_cli.fetch_latest_cli(ttl=0, station=station)  # always fresh in the cron
        today = settlement.climate_day_of(now, station)
        if not cli or cli["report_date"] != today:
            got = cli["report_date"].isoformat() if cli else None
            print(f"[{station}] CLI alert: no report for today ({today}) yet — latest is {got}")
            return
        state = alerts.load_state(state_path)
        if state.get("last_alerted_day") == today.isoformat():
            print(f"[{station}] CLI alert: already sent for {today}")
            return
        msg = (f'High {cli["high_f"]:g}°F · Low {cli["low_f"]:g}°F'
               f' · issued {cli["issued"].strftime("%-I:%M %p")}')
        if notify.send_ntfy(f"{name} Climate Report", msg):
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w") as fh:
                json.dump({"last_alerted_day": today.isoformat()}, fh)
            print(f"[{station}] CLI alert sent for {today}")
        else:
            print(f"[{station}] CLI alert: send_ntfy returned False (NTFY_TOPIC "
                  "unset or ntfy POST failed)")
    except Exception as e:
        print(f"[{station}] CLI alert skipped: {e}")


def _maybe_alert_resolved(snap: dict, now: datetime,
                          station: str = config.DEFAULT_STATION) -> None:
    """Ping once per variable per day the first time its displayed Resolved %
    reaches RESOLVED_ALERT_PCT. High and low fire independently. Best-effort —
    a failure logs and never blocks the surrounding logging."""
    try:
        import notify
        name = config.station(station).name
        state_path = _state_path(RESOLVED_STATE_PATH, "resolved_alert_state.json", station)
        today = settlement.climate_day_of(now, station).isoformat()
        state = alerts.load_state(state_path)
        dirty = False
        for var in ("high", "low"):
            d = (snap.get("today") or {}).get(var)
            if not d:
                continue
            # Dawn low still forming: the card's 50% cap was removed 2026-07-26,
            # so the clock-inflated current `resolved` can cross the alert
            # threshold before the trough physically locks. Don't push "Low
            # Locked In" until it does.
            if d.get("low_forming"):
                continue
            pct = model.displayed_resolved(d)
            if pct < RESOLVED_ALERT_PCT or state.get(var) == today:
                continue
            title = f"{name} {var.capitalize()} Locked In"
            body = f"{pct}% resolved · ≈{d['consensus']:g}°F"
            if notify.send_ntfy(title, body):
                state[var] = today
                dirty = True
                print(f"[{station}] Resolved alert sent: {var} {pct}%")
            else:
                print(f"[{station}] Resolved alert: send_ntfy False for {var} ({pct}%)")
        if dirty:
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w") as fh:
                json.dump(state, fh)
    except Exception as e:
        print(f"[{station}] Resolved alert skipped: {e}")


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
    """One scheduled run for a single station: CLI-report alert, calibration,
    det_models publish, model logging, and settlements — all routed to `code`.
    Raised errors propagate to `main`, which isolates each station."""
    from sources.common import TZ
    _maybe_alert_cli(datetime.now(TZ), code)
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
