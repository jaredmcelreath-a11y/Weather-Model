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

import calibration
import forecast_log
import model


def main() -> None:
    calib = calibration.get(refresh=True)
    forecast_log.record(model.snapshot(calib))                       # hourly basis
    off = (calib or {}).get("settlement_offset")
    forecast_log.record(model.snapshot(calib, settle_offset=off), basis="cli")
    n = len(forecast_log.load(forecast_log._PATH))
    print(f"logged hourly+cli snapshots; log now holds {n} records")


if __name__ == "__main__":
    main()
