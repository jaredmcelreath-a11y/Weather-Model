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

import betting_log
import calibration
import consensus_log
import forecast_log
import model
import settlements
from sources import kalshi


def main() -> None:
    calib = calibration.get(refresh=True)
    # The live site is Kalshi/CLI-only — log just the CLI snapshot. The hourly
    # (Robinhood) basis is retired from the site, so the scheduler no longer
    # grows an hourly cohort. (model.snapshot's hourly mode stays available.)
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
    # Betting-time capture: only when `now` falls in a betting slot (5x/day),
    # build the hourly center and record a slot-keyed row. Reuses cli_snap (with
    # its attached market) rather than re-fetching. Best-effort: an error here
    # doesn't block forecast/consensus logging above.
    try:
        from datetime import datetime as _dt
        from betting_log import TZ as _BTZ
        if betting_log.current_slot(_dt.now(_BTZ)) is not None:
            hourly_snap = model.snapshot(calib)
            slot = betting_log.capture_if_slot(cli_snap, hourly_snap, calib)
            print(f"betting-time capture at slot {slot}")
    except Exception as e:
        print(f"betting capture skipped: {e}")
    # Persist actual settlements for any forecast day that has now settled, on
    # both bases — the durable ground truth for historical accuracy. Best-effort:
    # an archive hiccup just leaves those days for the next run.
    try:
        settlements.record()
        s = len(settlements.load(settlements._PATH))
    except Exception as e:
        print(f"settlement recording skipped: {e}")
        s = len(settlements.load(settlements._PATH))
    n = len(forecast_log.load(forecast_log._PATH))
    print(f"logged cli snapshot; log now holds {n} records, {s} settlements")


if __name__ == "__main__":
    main()
