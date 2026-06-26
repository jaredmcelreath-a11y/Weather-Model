"""Persistent record of actual KDFW daily settlements (high/low).

forecast_log stores what the model *predicted*; this stores what actually
*happened*, so historical accuracy can be charted without re-fetching the IEM
archive every time — and so we keep a durable, append-only copy of the truth
even if that upstream archive later shifts or goes away.

One record per (target_date, basis): basis "hourly" mirrors the live/Robinhood
settlement (hourly METAR extremes, via station_history.fetch_actual) and "cli"
the NWS-CLI continuous daily max/min that Kalshi resolves on (fetch_actual_cli).
Append-once — a day already recorded for a basis is left untouched on later
runs; only genuinely new settled days are fetched and appended.

The set of days to settle comes from forecast_log: any target_date we forecast
that is now in the past. Storage and cloud behavior mirror forecast_log: a local
JSONL file, or a GitHub-hosted copy on the cloud deploy — read by the dashboard,
written solely by the scheduled Action. The fetch/parse helpers are reused.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

import forecast_log
from config import TIMEZONE
from forecast_log import _load_github, _parse
from sources import station_history
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)
_PATH = os.path.join(os.path.dirname(__file__), "settlements.jsonl")
BASES = ("hourly", "cli")


def _github_cfg() -> dict | None:
    """Remote-log config from env, pointing at the settlements file.

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
        "path": os.environ.get("FORECAST_LOG_GH_SETTLEMENTS_PATH", "settlements.jsonl"),
        "token": os.environ.get("FORECAST_LOG_GH_TOKEN") or None,
    }


def load(path: str | None = None) -> list[dict]:
    """All recorded settlements, oldest-written first.

    With no explicit path, transparently reads the GitHub-hosted file when the
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


def _settled_days(today: date) -> list[date]:
    """Distinct forecast target dates now in the past — the days worth settling."""
    days = {date.fromisoformat(r["target_date"]) for r in forecast_log.load()}
    return sorted(d for d in days if d < today)


def _fetch(basis: str, start: date, end: date) -> dict[date, tuple[float, float]]:
    fn = (station_history.fetch_actual_cli if basis == "cli"
          else station_history.fetch_actual)
    return fn(start, end)


def record(today: date | None = None, path: str | None = None) -> None:
    """Fetch and append actual settlements for newly settled days.

    For each basis, finds the settled forecast days not yet recorded, fetches
    their actual high/low in one ranged call, and appends a record per day that
    the archive can answer for (a day still missing upstream is silently left
    for a later run). No-op on the cloud deploy (remote log configured, no
    explicit path): there the scheduled Action is the sole writer.
    """
    if path is None and _github_cfg() is not None:
        return
    today = today or date.today()
    settled = _settled_days(today)
    if not settled:
        return

    target_path = path or _PATH
    rows = load(target_path)
    have = {(r["target_date"], r.get("basis", "hourly")) for r in rows}
    recorded_at = datetime.now(TZ).isoformat(timespec="seconds")

    new: list[dict] = []
    for basis in BASES:
        missing = [d for d in settled if (d.isoformat(), basis) not in have]
        if not missing:
            continue
        actual = _fetch(basis, min(missing), max(missing))
        for d in missing:
            hl = actual.get(d)
            if not hl:
                continue
            new.append({
                "target_date": d.isoformat(),
                "basis": basis,
                "high": hl[0],
                "low": hl[1],
                "recorded_at": recorded_at,
            })

    if new:
        rows.extend(new)
        rows.sort(key=lambda r: (r["target_date"], r.get("basis", "hourly")))
        _write(rows, target_path)


def as_map(basis: str = "hourly", path: str | None = None) -> dict[date, tuple[float, float]]:
    """{day: (high, low)} for one basis — the durable counterpart to
    station_history.fetch_actual, served from the persisted log."""
    out: dict[date, tuple[float, float]] = {}
    for r in load(path):
        if r.get("basis", "hourly") != basis:
            continue
        try:
            out[date.fromisoformat(r["target_date"])] = (r["high"], r["low"])
        except (KeyError, ValueError):
            continue
    return out
