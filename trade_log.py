"""Audit-record schema for the trading loop. trade_state owns the GitHub IO;
this builds the typed records appended to trade_log.jsonl."""
from __future__ import annotations

from datetime import datetime, timezone


def build_record(kind: str, **fields) -> dict:
    """One audit row: kind in {entry, exit, skip, halt}, plus arbitrary fields."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind}
    rec.update(fields)
    return rec
