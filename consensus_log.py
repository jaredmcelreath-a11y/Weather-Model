"""Intraday history of the model's consensus, for the 'consensus through the day' chart.

Unlike forecast_log (which upserts one row per lead bucket, keeping only the
latest capture), this keeps a *time series*: one point roughly every 10 minutes
per (target_date, variable, basis) so the dashboard can chart how the consensus
drifts through the day as the peak/trough approaches. Records whose target_date
is already in the past are pruned on write, so the file stays tiny (only today
and tomorrow are ever charted).

Storage and cloud behavior mirror forecast_log: a local JSONL file, or a
GitHub-hosted copy on the cloud deploy — read by the dashboard, written solely
by the scheduled Action. The GitHub fetch/parse helpers are reused from there.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from config import TIMEZONE
from forecast_log import _load_github, _parse
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)
_PATH = os.path.join(os.path.dirname(__file__), "consensus_history.jsonl")
# Throttle floor per series. Must sit comfortably below the 10-min logging cron
# (.github/workflows/log.yml): if the throttle equals the cadence, normal Actions
# startup jitter pushes runs a few seconds under the interval and drops every
# other point, collapsing the chart to double spacing. 7 min keeps every
# scheduled run (even one arriving ~3 min early) while still throttling an
# always-open local dashboard that refreshes every minute.
MIN_INTERVAL_MIN = 7


def _github_cfg() -> dict | None:
    """Remote-log config from env, pointing at the consensus history file.

    Shares the repo/ref/token with forecast_log (set from Streamlit secrets);
    only the file path differs. Present on the cloud deploy, absent locally and
    in the scheduled Action — both of which work the local file directly.
    """
    repo = os.environ.get("FORECAST_LOG_GH_REPO")
    if not repo:
        return None
    return {
        "repo": repo,
        "ref": os.environ.get("FORECAST_LOG_GH_REF", "data"),
        "path": os.environ.get("FORECAST_LOG_GH_CONSENSUS_PATH", "consensus_history.jsonl"),
        "token": os.environ.get("FORECAST_LOG_GH_TOKEN") or None,
    }


def load(path: str | None = None) -> list[dict]:
    """All logged samples, oldest-written first.

    With no explicit path, transparently reads the GitHub-hosted history when the
    dashboard has configured one (cloud deploy); otherwise the local file. An
    explicit path always reads locally (used by record() and the Action).
    """
    if path is None:
        cfg = _github_cfg()
        if cfg:
            return _load_github(cfg)
    path = path or _PATH
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return _parse(fh.read())


def _write(rows: list[dict], path: str) -> None:
    with open(path, "w") as fh:
        for rec in rows:
            fh.write(json.dumps(rec) + "\n")


def record(snapshot: dict, path: str | None = None, basis: str = "hourly") -> None:
    """Append a consensus sample for today+tomorrow, throttled per series.

    A point is added only when the most recent sample for that (target_date,
    variable, basis) is at least MIN_INTERVAL_MIN old, so an always-open
    dashboard (refreshing every minute) doesn't flood the file. Past target
    dates are pruned. No-op on the cloud deploy (remote log configured, no
    explicit path): there the scheduled Action is the sole writer.
    """
    if path is None and _github_cfg() is not None:
        return
    captured = snapshot.get("updated") or datetime.now(TZ).isoformat(timespec="seconds")
    now = datetime.fromisoformat(captured)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    today_iso = now.date().isoformat()

    current = snapshot.get("current") or {}
    current_temp = current.get("temp")

    target_path = path or _PATH
    rows = load(target_path)
    orig_len = len(rows)
    # Keep only today/tomorrow — all the chart ever shows.
    rows = [r for r in rows if r.get("target_date", "") >= today_iso]
    pruned = len(rows) != orig_len

    # Most recent sample time per series, for the throttle.
    last: dict[tuple, str] = {}
    for r in rows:
        k = (r["target_date"], r["variable"], r.get("basis", "hourly"))
        t = r.get("captured_at")
        if t and (k not in last or t > last[k]):
            last[k] = t

    appended = False
    for which in ("today", "tomorrow"):
        pred = snapshot.get(which)
        if not pred:
            continue
        target_date = pred["day"]
        for variable in ("high", "low"):
            d = pred.get(variable)
            if not d or d.get("consensus") is None:
                continue
            prev = last.get((target_date, variable, basis))
            if prev:
                prev_dt = datetime.fromisoformat(prev)
                if prev_dt.tzinfo is None:
                    prev_dt = prev_dt.replace(tzinfo=TZ)
                if now - prev_dt < timedelta(minutes=MIN_INTERVAL_MIN):
                    continue
            rec = {
                "target_date": target_date,
                "variable": variable,
                "basis": basis,
                "captured_at": captured,
                "consensus": d.get("consensus"),
            }
            # The live temperature at capture time, so today's chart can show the
            # actual reading converging on the predicted peak/trough. Only
            # meaningful for today — tomorrow's curve hasn't happened yet.
            if target_date == today_iso and current_temp is not None:
                rec["current_temp"] = current_temp
            # The Kalshi market's implied extreme at this capture, so the chart can
            # trace the market's own forecast through the day alongside ours.
            # Present only on the CLI snapshot (where scheduled_log attaches the
            # market block); absent on hourly, so the line shows on the Kalshi page.
            mev = (snapshot.get("market", {}).get(which, {}).get(variable) or {}).get("ev")
            if mev is not None:
                rec["market_ev"] = mev
            rows.append(rec)
            appended = True

    if appended or pruned:
        _write(rows, target_path)
