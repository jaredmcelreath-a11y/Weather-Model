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

from datetime import date

import calibration
import consensus_log
import forecast_log
import model
from sources import kalshi


def main() -> None:
    calib = calibration.get(refresh=True)
    hourly_snap = model.snapshot(calib)                              # hourly basis
    forecast_log.record(hourly_snap)
    consensus_log.record(hourly_snap)
    off = (calib or {}).get("settlement_offset")
    cli_snap = model.snapshot(calib, settle_offset=off, continuous_obs=True)
    # Attach the live Kalshi market's implied forecast to the CLI snapshot, so the
    # log can later score market-vs-model against settlement. Best-effort: a market
    # outage just omits the block and the model logging is unaffected.
    try:
        today = date.fromisoformat(cli_snap["today"]["day"])
        tomorrow = date.fromisoformat(cli_snap["tomorrow"]["day"])
        cli_snap["market"] = kalshi.implied_block(today, tomorrow)
    except Exception as e:
        print(f"market block skipped: {e}")
    forecast_log.record(cli_snap, basis="cli")
    consensus_log.record(cli_snap, basis="cli")
    n = len(forecast_log.load(forecast_log._PATH))
    print(f"logged hourly+cli snapshots; log now holds {n} records")


if __name__ == "__main__":
    main()
