"""GitHub-branch-backed store for trading parameters, runtime, and the audit log.

All trade documents live on a dedicated branch (default `trade-data`), read and
written through the GitHub contents API so both the Streamlit control page and
the headless cron share one source without any git operations. Kept off the
`data` branch so log.yml's force-push cannot clobber it.

`trade_state.json`  — params; written only by the control page, read by the cron.
`trade_runtime.json`— cron-owned mutable runtime (halt day, per-position entry_ask).
`trade_log.jsonl`   — append-only audit.
"""
from __future__ import annotations

import base64
import json
import os

import requests

import trade_params

STATE_PATH = "trade_state.json"
RUNTIME_PATH = "trade_runtime.json"
LOG_PATH = "trade_log.jsonl"


class ConflictError(RuntimeError):
    """A PUT lost the optimistic-concurrency race (stale sha)."""


class GitHubTransport:
    """GET/PUT a file on the trade-data branch via the contents API."""

    def __init__(self):
        self.repo = os.environ.get("TRADE_GH_REPO", "")
        self.branch = os.environ.get("TRADE_GH_BRANCH", "trade-data")
        self.token = os.environ.get("TRADE_GH_TOKEN", "")

    def _headers(self):
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _url(self, path):
        return f"https://api.github.com/repos/{self.repo}/contents/{path}"

    def get(self, path):
        """Return (text, sha) or None if the file does not exist."""
        r = requests.get(self._url(path), params={"ref": self.branch},
                         headers=self._headers(), timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        j = r.json()
        text = base64.b64decode(j["content"]).decode("utf-8")
        return text, j["sha"]

    def put(self, path, text, sha):
        body = {
            "message": f"trade: update {path}",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        r = requests.put(self._url(path), json=body, headers=self._headers(),
                         timeout=15)
        if r.status_code == 409:
            raise ConflictError(path)
        r.raise_for_status()


def _t(transport):
    return transport or GitHubTransport()


def _read_json(path, transport):
    got = _t(transport).get(path)
    if not got:
        return None, None
    text, sha = got
    return (json.loads(text) if text.strip() else None), sha


def load_state(transport=None) -> dict:
    data, _sha = _read_json(STATE_PATH, transport)
    return trade_params.merge_params(data)


def save_state(params: dict, transport=None) -> None:
    t = _t(transport)
    got = t.get(STATE_PATH)
    sha = got[1] if got else None
    t.put(STATE_PATH, json.dumps(params, indent=2, sort_keys=True), sha)


def load_runtime(transport=None) -> dict:
    data, _sha = _read_json(RUNTIME_PATH, transport)
    return data or {}


def save_runtime(runtime: dict, transport=None) -> None:
    t = _t(transport)
    got = t.get(RUNTIME_PATH)
    sha = got[1] if got else None
    t.put(RUNTIME_PATH, json.dumps(runtime, indent=2, sort_keys=True), sha)


def append_jsonl(path: str, record: dict, transport=None) -> None:
    t = _t(transport)
    got = t.get(path)
    text, sha = (got[0], got[1]) if got else ("", None)
    text = (text + json.dumps(record) + "\n") if text else json.dumps(record) + "\n"
    t.put(path, text, sha)
