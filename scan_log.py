"""Record schema and branch-backed IO for the multi-city price scanner.

Two append-only files on a dedicated `scan-data` branch:

`scan_log.jsonl`     — one row per bracket per snapshot firing (price + strike).
`scan_settled.jsonl` — one row per bracket, Kalshi's own settlement result.

They are joined on `ticker` at report time, the same shape as
betting_log.jsonl + settlements.jsonl.

The transport duplicates trade_state.GitHubTransport rather than importing it.
That is deliberate: the trader's IO is load-bearing for real money, and reusing
it would create a path where a scanner change breaks trading. Different env
vars, different branch, no shared code.
"""
from __future__ import annotations

import base64
import json
import os

import requests

from sources.kalshi import parse_kalshi_ts

SNAPSHOT_PATH = "scan_log.jsonl"
SETTLED_PATH = "scan_settled.jsonl"
CANDIDATES_PATH = "scan_candidates.jsonl"


class GitHubTransport:
    """GET/PUT a file on the scan-data branch via the contents API."""

    def __init__(self):
        self.repo = os.environ.get("SCAN_GH_REPO", "")
        self.branch = os.environ.get("SCAN_GH_BRANCH", "scan-data")
        self.token = os.environ.get("SCAN_GH_TOKEN", "")

    def _headers(self):
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _url(self, path):
        return f"https://api.github.com/repos/{self.repo}/contents/{path}"

    def get(self, path):
        r = requests.get(self._url(path), params={"ref": self.branch},
                         headers=self._headers(), timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        j = r.json()
        return base64.b64decode(j["content"]).decode("utf-8"), j["sha"]

    def put(self, path, text, sha):
        body = {"message": f"scan: update {path}", "branch": self.branch,
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        r = requests.put(self._url(path), json=body,
                         headers=self._headers(), timeout=30)
        r.raise_for_status()


def _t(transport=None):
    return transport or GitHubTransport()


def variable_of_series(series: str):
    """'high'/'low' from the series prefix, or None for a non-temperature series."""
    s = (series or "").upper()
    if s.startswith("KXHIGH"):
        return "high"
    if s.startswith("KXLOW"):
        return "low"
    return None


def hours_to_close(close_time, now):
    """Hours from `now` until the market closes, or None when unparseable.

    This is the scanner's time axis instead of local clock time: it needs no
    per-city timezone table, and '12 hours before settlement' is comparable
    across cities in a way that '13:00 local' is not."""
    closed = parse_kalshi_ts(close_time)
    if closed is None:
        return None
    return round((closed - now).total_seconds() / 3600.0, 2)


def _dollars(value):
    """Kalshi returns prices and sizes as dollar STRINGS ("0.3300"); cast to
    float. None for absent or unparseable values — mirrors sources.kalshi._f."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_snapshot_row(market: dict, series: str, now):
    """One priced bracket at one moment, or None when the market has no quotes.

    Prices come from the *_dollars fields and volume from volume_fp — the bare
    yes_bid/yes_ask/volume keys do not exist on this endpoint. Reading the wrong
    names is silent: every field is None, every row is dropped as unquoted, and
    a live pass returns 0 rows from 40 active series (measured 2026-08-03).

    An unquoted market is dropped, not stored: recording a missing bid as 0 would
    look like a free option in the reliability curve. A genuine 0.00 bid on a
    dead tail still records — the report's price bands exclude it."""
    bid = _dollars(market.get("yes_bid_dollars"))
    ask = _dollars(market.get("yes_ask_dollars"))
    if bid is None and ask is None:
        return None
    return {
        "ts": now.isoformat().replace("+00:00", "Z"),
        "series": series,
        "variable": variable_of_series(series),
        "ticker": market.get("ticker"),
        "strike_type": market.get("strike_type"),
        # Kalshi's OWN wording ("91° or above"), not one we invent. A tail's
        # strike sits a degree outside the range it pays on, so any label built
        # from floor/cap alone disagrees with what the user sees on Kalshi.
        "label": market.get("yes_sub_title") or market.get("subtitle"),
        "floor": market.get("floor_strike"),
        "cap": market.get("cap_strike"),
        "yes_bid": bid,
        "yes_ask": ask,
        "volume": _dollars(market.get("volume_fp")),
        "close_time": market.get("close_time"),
        "hours_to_close": hours_to_close(market.get("close_time"), now),
    }


def build_settlement_row(market: dict, now):
    """Kalshi's own outcome for a bracket, or None if it has not finalized.

    Only `finalized` counts — a market can be `settled` and still revise."""
    if (market.get("status") or "").lower() != "finalized":
        return None
    result = (market.get("result") or "").lower()
    if result not in ("yes", "no"):
        return None
    return {"ticker": market.get("ticker"), "result": result,
            "settled_at": now.isoformat().replace("+00:00", "Z")}


def load(path: str, transport=None) -> list:
    """Every row in `path`, oldest first; [] when the file does not exist."""
    got = _t(transport).get(path)
    if not got:
        return []
    return [json.loads(l) for l in got[0].splitlines() if l.strip()]


def append_many(path: str, rows: list, transport=None) -> int:
    """Append every row in ONE read + ONE write.

    trade_state.append_jsonl does a GET+PUT per record, which is fine for the
    trader's handful of rows a day. A snapshot pass writes ~600 at once, so
    per-record round trips would mean 600 API calls and 600 commits per firing.
    """
    if not rows:
        return 0
    t = _t(transport)
    got = t.get(path)
    text, sha = (got[0], got[1]) if got else ("", None)
    payload = "".join(json.dumps(r) + "\n" for r in rows)
    t.put(path, (text + payload) if text else payload, sha)
    return len(rows)
