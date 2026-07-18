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

from datetime import date, datetime

import betting_log
import calibration
import consensus_log
import forecast_log
import model
import settlements
from sources import kalshi


def _record_settlements() -> int:
    """Persist actual settlements for any settled forecast day — the one job
    that needs no calibration, so it runs even when the model logging is
    skipped. Best-effort: an archive hiccup just leaves days for the next run."""
    try:
        settlements.record()
    except Exception as e:
        print(f"settlement recording skipped: {e}")
    return len(settlements.load(settlements._PATH))


def _log_snapshots(calib: dict, off) -> None:
    """The model-logging body of a scheduled run: CLI snapshot + market block +
    forecast/consensus logs + the slot-gated betting capture."""
    cli_snap = model.snapshot(calib, settle_offset=off, continuous_obs=True,
                              include_candidate=True)
    # Attach the live Kalshi market's implied forecast so the log can later
    # score market-vs-model against settlement. Best-effort: a market outage
    # just omits the block and the model logging is unaffected.
    try:
        today = date.fromisoformat(cli_snap["today"]["day"])
        tomorrow = date.fromisoformat(cli_snap["tomorrow"]["day"])
        cli_snap["market"] = kalshi.implied_block(today, tomorrow)
    except Exception as e:
        print(f"market block skipped: {e}")
    forecast_log.record(cli_snap, basis="cli")
    consensus_log.record(cli_snap, basis="cli")
    # Betting-time capture: only when `now` falls in a betting slot (5x/day).
    # Best-effort: an error here doesn't block the logging above.
    try:
        from betting_log import TZ as _BTZ
        if betting_log.current_slot(datetime.now(_BTZ)) is not None:
            hourly_snap = model.snapshot(calib)
            slot = betting_log.capture_if_slot(cli_snap, hourly_snap, calib)
            print(f"betting-time capture at slot {slot}")
    except Exception as e:
        print(f"betting capture skipped: {e}")


def main() -> None:
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
