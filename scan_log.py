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
from datetime import datetime, timedelta, timezone

import requests

from sources.kalshi import parse_kalshi_ts

SNAPSHOT_PATH = "scan_log.jsonl"
SETTLED_PATH = "scan_settled.jsonl"
CANDIDATES_PATH = "scan_candidates.jsonl"
REFERENCE_PATH = "screen_reference.json"      # screen.py -> screen_alert.py
ALERT_STATE_PATH = "screen_alert_state.json"  # tickers already pushed, by day


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

    def get(self, path, _requests_get=None):
        """(text, sha) for `path`, or None when it does not exist.

        Above 1 MB the JSON tier answers with the metadata and an EMPTY
        `content` field rather than an error. Decoding that gives "", which
        append_many reads as an empty file and then PUTs over — silently
        destroying the whole log. So when content is absent, re-fetch through
        the raw media type, which serves files up to 100 MB. The sha still comes
        from the JSON response; the raw response does not carry one."""
        get = _requests_get or requests.get
        r = get(self._url(path), params={"ref": self.branch},
                headers=self._headers(), timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        j = r.json()
        if j.get("content"):
            return base64.b64decode(j["content"]).decode("utf-8"), j["sha"]
        headers = dict(self._headers(), Accept="application/vnd.github.raw+json")
        raw = get(self._url(path), params={"ref": self.branch},
                  headers=headers, timeout=30)
        raw.raise_for_status()
        return raw.text, j["sha"]

    def list_dir(self, path):
        """Names of the files directly under `path`; [] when there is no such
        directory (the contents API 404s on a path that has never been written)."""
        r = requests.get(self._url(path), params={"ref": self.branch},
                         headers=self._headers(), timeout=15)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        items = r.json()
        if not isinstance(items, list):
            return []
        return sorted(i["name"] for i in items if i.get("type") == "file")

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


def dollars(value):
    """Kalshi returns prices and sizes as dollar STRINGS ("0.3300"); cast to
    float. None for absent or unparseable values — mirrors sources.kalshi._f."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_dollars = dollars   # pre-existing internal name; keep both pointing at one function


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


def day_path(path: str, when: datetime) -> str:
    """The daily partition `when`'s rows belong in ('scan_log/2026-08-04.jsonl').

    Every append rewrites the WHOLE file through the contents API, so a single
    growing log costs quadratically: at ~480 rows a firing the snapshot log put
    on ~0.4 MB a day, and by month's end each of the day's appends would have
    read and rewritten megabytes and stored another blob that size in git. A
    daily file stays small forever, and each firing's commit is small too."""
    stem = path[:-len(".jsonl")] if path.endswith(".jsonl") else path
    return f"{stem}/{when:%Y-%m-%d}.jsonl"


def _row_time(row: dict):
    """A row's own timestamp, or None when it has none we can read."""
    try:
        return datetime.fromisoformat(str(row.get("ts")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _read(path: str, transport) -> list:
    got = transport.get(path)
    if not got:
        return []
    return [json.loads(l) for l in got[0].splitlines() if l.strip()]


def load(path: str, transport=None) -> list:
    """Every row ever written, oldest first.

    The pre-split flat file comes first and is never written again — but it
    still holds real history, so it is read, not migrated."""
    t = _t(transport)
    stem = path[:-len(".jsonl")] if path.endswith(".jsonl") else path
    out = _read(path, t)
    for name in t.list_dir(stem):
        if name.endswith(".jsonl"):
            out.extend(_read(f"{stem}/{name}", t))
    return out


def load_recent(path: str, days: int = 3, transport=None, now=None) -> list:
    """Rows from the last `days` daily partitions, oldest first.

    What a page wants: reading every partition would cost a directory listing
    plus a request per day, forever. Legacy flat-file rows are filtered by their
    own timestamp, since that file spans everything before the split."""
    now = now or datetime.now(timezone.utc)
    t = _t(transport)
    cutoff = now - timedelta(days=days)
    out = [r for r in _read(path, t)
           if (_row_time(r) or cutoff) >= cutoff]
    for i in range(days - 1, -1, -1):                    # oldest day first
        out.extend(_read(day_path(path, now - timedelta(days=i)), t))
    return out


def read_doc(path: str, transport=None) -> dict:
    """A whole-file JSON document, or {} when absent, corrupt or not an object.

    The snapshot and candidate logs are append-only JSONL partitions; the
    screen's reference and the alerter's state are single small documents that
    are REPLACED on every write, so they get their own pair. Never raises: both
    readers run in a cron whose job is to stay quietly reliable, and a
    half-written document must read as "nothing yet"."""
    got = _t(transport).get(path)
    if not got:
        return {}
    try:
        obj = json.loads(got[0])
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def write_doc(path: str, obj: dict, transport=None) -> None:
    """Replace `path` with `obj`. One GET (for the sha) plus one PUT."""
    t = _t(transport)
    got = t.get(path)
    t.put(path, json.dumps(obj), got[1] if got else None)


def append_many(path: str, rows: list, transport=None, now=None) -> int:
    """Append every row to its day's partition in ONE read + ONE write.

    trade_state.append_jsonl does a GET+PUT per record, which is fine for the
    trader's handful of rows a day. A snapshot pass writes ~600 at once, so
    per-record round trips would mean 600 API calls and 600 commits per firing.

    The partition comes from the ROWS' own timestamp where they carry one: a
    pass that begins at 23:59 and writes after midnight belongs with the firing
    it sampled, not the clock that happened to tick over mid-write."""
    if not rows:
        return 0
    t = _t(transport)
    when = _row_time(rows[0]) or now or datetime.now(timezone.utc)
    target = day_path(path, when)
    got = t.get(target)
    text, sha = (got[0], got[1]) if got else ("", None)
    payload = "".join(json.dumps(r) + "\n" for r in rows)
    t.put(target, (text + payload) if text else payload, sha)
    return len(rows)
