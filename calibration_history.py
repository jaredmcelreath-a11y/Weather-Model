"""Append-only history of calibration recomputes, for the drift view.

calibration.json is overwritten on each recompute, so it holds no history — you
can't see whether the learned bias/sigma/offset have been stable or sliding. This
log appends one flattened row per genuine recompute (~1x/day), keyed on the
`computed` timestamp so a restore-then-recompute can't double-log.

Storage/cloud behavior mirrors forecast_log and settlements: a local JSONL file,
or a GitHub-hosted copy on the cloud deploy — read by the dashboard, written
solely by the scheduled Action. The GitHub helpers are reused.
"""

from __future__ import annotations

import json
import os

from forecast_log import _load_github, _parse

_PATH = os.path.join(os.path.dirname(__file__), "calibration_history.jsonl")


def flatten(calib: dict) -> dict:
    """The tracked scalars for one calibration, pulled out of the nested dict.
    Missing pieces read as None so an older/partial calibration still logs a row."""
    calib = calib or {}
    det = ((calib.get("bias") or {}).get("deterministic") or {})
    sigma = calib.get("sigma") or {}
    settle = calib.get("settlement_offset") or {}
    cooling = calib.get("cooling") or {}
    bc = calib.get("bias_correction") or {}
    lead24 = ((bc.get("by_lead") or {}).get("24")
              or (bc.get("by_lead") or {}).get(24) or {})
    warm = bc.get("warm_low") or {}
    return {
        "computed": calib.get("computed"),
        "n_days": calib.get("n_days"),
        "bias_high": det.get("high"),
        "bias_low": det.get("low"),
        "sigma_high": sigma.get("high"),
        "sigma_low": sigma.get("low"),
        "settle_high": settle.get("high"),
        "settle_low": settle.get("low"),
        "cooling_low": cooling.get("low_offset"),
        "corr_lead24_high": lead24.get("high"),
        "corr_lead24_low": lead24.get("low"),
        "corr_warm_low": warm.get("bias"),
    }


def _github_cfg() -> dict | None:
    """Remote-log config from env, pointing at the calibration-history file.
    Shares repo/ref/token with forecast_log; only the path differs. Present on the
    cloud deploy, absent locally and in the Action (both work the local file)."""
    repo = os.environ.get("FORECAST_LOG_GH_REPO")
    if not repo:
        return None
    return {
        "repo": repo,
        "ref": os.environ.get("FORECAST_LOG_GH_REF", "data"),
        "path": os.environ.get("FORECAST_LOG_GH_CALIB_HISTORY_PATH",
                               "calibration_history.jsonl"),
        "token": os.environ.get("FORECAST_LOG_GH_TOKEN") or None,
    }


def load(path: str | None = None) -> list[dict]:
    """All history rows, oldest first. Reads the GitHub-hosted file on the cloud
    (no explicit path), else the local file. Missing source -> []."""
    if path is None:
        cfg = _github_cfg()
        if cfg:
            return _load_github(cfg)
    path = path or _PATH
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return _parse(fh.read())


def record(calib: dict, path: str | None = None) -> None:
    """Append this calibration's flattened row. Deduped on `computed` (skip if the
    latest row already has that stamp). No-op on the cloud deploy (remote log
    configured, no explicit path): there the Action is the sole writer."""
    if path is None and _github_cfg() is not None:
        return
    row = flatten(calib)
    target = path or _PATH
    rows = load(target)
    if rows and rows[-1].get("computed") == row.get("computed"):
        return
    with open(target, "a") as fh:
        fh.write(json.dumps(row) + "\n")
