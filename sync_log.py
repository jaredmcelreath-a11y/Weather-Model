"""Pull the cloud forecast log into the local file.

The live dashboard's self-scoring reads forecast_log.jsonl from the repo's `data`
branch (maintained by the scheduled GitHub Action in .github/workflows/log.yml).
Locally the dashboard reads the on-disk file, which goes stale whenever this
machine is off. Run this to refresh local scoring with the cloud history:

    python sync_log.py

Merges by the log's (target_date, variable, lead_bucket) key, with the `data`
branch winning on overlap (it's the authoritative, always-running copy), so any
predictions you logged only locally are preserved. Backs up the current file to
forecast_log.jsonl.bak first. Needs the `origin` git remote.
"""

from __future__ import annotations

import shutil
import subprocess

import forecast_log


def _data_branch_rows() -> list[dict]:
    """The forecast log as it currently stands on origin/data."""
    subprocess.run(["git", "fetch", "origin", "data"], check=True,
                   capture_output=True)
    text = subprocess.run(["git", "show", "origin/data:forecast_log.jsonl"],
                          check=True, capture_output=True, text=True).stdout
    return forecast_log._parse(text)


def main() -> None:
    local = forecast_log.load(forecast_log._PATH)
    remote = _data_branch_rows()

    merged = {forecast_log._key(r): r for r in local}
    merged.update({forecast_log._key(r): r for r in remote})  # data branch wins
    rows = sorted(merged.values(),
                  key=lambda r: (r.get("captured_at", ""), r["target_date"],
                                 r["variable"]))

    try:
        shutil.copy(forecast_log._PATH, forecast_log._PATH + ".bak")
    except FileNotFoundError:
        pass
    forecast_log._write(rows, forecast_log._PATH)

    dates = sorted({r["target_date"] for r in rows})
    span = f"{dates[0]}..{dates[-1]}" if dates else "empty"
    print(f"synced {len(local)} local + {len(remote)} cloud -> {len(rows)} "
          f"records across {len(dates)} dates ({span})")


if __name__ == "__main__":
    main()
