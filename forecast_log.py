"""Forward log of the model's own predictions.

Every snapshot the dashboard shows is appended here so we can later score the
model against what KDFW actually settled at — the only honest way to know
whether the probabilities are calibrated (does "70%" really hit 70%?) and to
derive empirical per-lead-time spread once enough days accumulate.

One record per (target_date, variable, lead_bucket). Re-running the same day
upserts in place (keeps the latest capture for that bucket) so an always-open
dashboard doesn't bloat the log with near-duplicate rows. Stored as JSONL; the
file is tiny (a few rows per day) so rewrite-on-upsert is fine.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from config import TIMEZONE, lead_bucket
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)
_PATH = os.path.join(os.path.dirname(__file__), "forecast_log.jsonl")


def _key(rec: dict) -> tuple:
    return (rec["target_date"], rec["variable"], rec["lead_bucket"],
            rec.get("basis", "hourly"))


def _github_cfg() -> dict | None:
    """Remote-log config from env (the dashboard sets it from Streamlit secrets).

    Present only on the cloud deploy; absent locally and in the scheduled Action,
    which both work the local file directly.
    """
    repo = os.environ.get("FORECAST_LOG_GH_REPO")
    if not repo:
        return None
    return {
        "repo": repo,
        "ref": os.environ.get("FORECAST_LOG_GH_REF", "data"),
        "path": os.environ.get("FORECAST_LOG_GH_PATH", "forecast_log.jsonl"),
        "token": os.environ.get("FORECAST_LOG_GH_TOKEN") or None,
    }


def _parse(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _load_github(cfg: dict) -> list[dict]:
    """Fetch the log file from a (private) GitHub branch via the Contents API."""
    import requests
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{cfg['path']}"
    headers = {"Accept": "application/vnd.github.raw+json"}
    if cfg["token"]:
        headers["Authorization"] = f"Bearer {cfg['token']}"
    r = requests.get(url, params={"ref": cfg["ref"]}, headers=headers, timeout=15)
    if r.status_code == 404:
        return []  # the data branch / log file doesn't exist yet
    r.raise_for_status()
    return _parse(r.text)


def load(path: str | None = None) -> list[dict]:
    """All logged records, oldest-written first.

    With no explicit path, transparently reads the GitHub-hosted log when the
    dashboard has configured one (cloud deploy); otherwise the local file. An
    explicit path always reads locally (used by record() and the Action). Missing
    source -> [].
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


def _source_means(per_source: dict, variable: str) -> dict:
    """Collapse {group: {label: (high, low)}} to {key: mean extreme} for one
    variable — the per-source predicted value we later difference against the
    settlement to learn each source's own bias. MOS models (the 'guidance'
    group) are emitted PER MODEL (mos_lav/mos_nbs) rather than collapsed, so live
    day-ahead skill-weighting can distinguish them; every other group collapses
    to its mean as before."""
    idx = 0 if variable == "high" else 1
    out = {}
    for group, labels in (per_source or {}).items():
        if group == "guidance":
            for label, v in labels.items():
                if v and v[idx] is not None:
                    out[label] = round(v[idx], 2)
            continue
        vals = [v[idx] for v in labels.values() if v and v[idx] is not None]
        if vals:
            out[group] = round(sum(vals) / len(vals), 2)
    return out


def record(snapshot: dict, path: str | None = None, basis: str = "hourly") -> None:
    """Upsert the snapshot's today+tomorrow predictions into the log.

    No-op on the cloud deploy (remote log configured, no explicit path): there
    the scheduled GitHub Action is the sole writer, so the dashboard must not
    clobber it with an ephemeral local copy.
    """
    if path is None and _github_cfg() is not None:
        return
    captured = snapshot.get("updated") or datetime.now(TZ).isoformat(timespec="seconds")
    now = datetime.fromisoformat(captured)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)

    sources = snapshot.get("sources", {})
    market = snapshot.get("market", {})
    new_recs = []
    for which in ("today", "tomorrow"):
        pred = snapshot.get(which)
        if not pred:
            continue
        target = datetime.fromisoformat(pred["day"]).date()
        bucket = lead_bucket(now, target)
        for variable in ("high", "low"):
            d = pred.get(variable)
            if not d or not d.get("probabilities"):
                continue
            rec = {
                "target_date": pred["day"],
                "variable": variable,
                "lead_bucket": bucket,
                "basis": basis,
                "captured_at": captured,
                "consensus": d.get("consensus"),
                "probabilities": d["probabilities"],
            }
            # Storm/front regime stamps — attribution for the correction
            # estimators (scoring drops flagged records from its residual
            # pool). Written only when set, so calm-day rows are unchanged
            # and historical rows read as unflagged via .get().
            for flag in ("convective_widened", "front_widened"):
                if d.get(flag):
                    rec[flag] = True
            # Applied self-correction knobs (by_lead / warm_low) baked into this
            # row's consensus — recorded so scoring can back them out and not
            # re-derive a correction from its own already-corrected forecast.
            # Only when non-empty (calm/obs-anchored rows carry none).
            corr = d.get("corrections")
            if corr:
                rec["corrections"] = corr
            # Per-source predicted extremes — present once the snapshot carries
            # them; lets scoring later learn ensemble/NWS bias from the live log.
            src = _source_means(sources.get(which, {}), variable)
            if src:
                rec["sources"] = src
            # The live market's own implied forecast at log time, so we can later
            # score market-vs-model against settlement (CLI snapshots only).
            mkt = market.get(which, {}).get(variable)
            if mkt:
                rec["market"] = mkt
            new_recs.append(rec)

    target_path = path or _PATH
    rows = load(target_path)
    index = {_key(r): i for i, r in enumerate(rows)}
    for rec in new_recs:
        k = _key(rec)
        if k in index:
            # Latch regime flags across upserts: a day the guard fired on at
            # ANY capture stays flagged even if the storm passed before this
            # final capture un-fired it — the correction pool excludes by
            # "was this a regime day", not "was the guard firing at 11:45pm".
            for flag in ("convective_widened", "front_widened"):
                if rows[index[k]].get(flag):
                    rec[flag] = True
            rows[index[k]] = rec
        else:
            index[k] = len(rows)
            rows.append(rec)
    _write(rows, target_path)
