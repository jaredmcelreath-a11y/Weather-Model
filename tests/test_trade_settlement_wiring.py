"""The trader Action must actually HAVE the settlement data its settlement pass
scores against.

`settle_positions` was correct and well covered, but in the deployed Action it was
fed an empty map on every run: `settlements.jsonl` is gitignored, so it is absent
from a fresh checkout, and trade.yml sets none of the FORECAST_LOG_GH_* vars that
would make `settlements.load` read the data branch instead. Nothing raised — the
map was simply `{}`, every past-day position took the "not settled yet, retry next
run" branch, and positions accumulated in `runtime["entries"]` forever. Two days of
held-to-settlement positions sat open in both cities, and one was eventually dumped
by the reversal path into a settled market's empty book at 0.01 (KAUS 2026-07-29,
booked -0.69 on a bracket that should have been scored against the CLI) — exactly
the failure `settle_positions` exists to prevent.

These tests pin the wiring, which the pure-logic tests cannot see.
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import paths
import trader
from tests.test_trader import Deps, _high_snap, _live_params

_WF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   ".github", "workflows", "trade.yml")
_NOW = datetime(2026, 7, 31, 10, 0, tzinfo=ZoneInfo("America/Chicago"))


def _workflow() -> str:
    with open(_WF) as fh:
        return fh.read()


def test_trade_workflow_restores_settlements_for_every_station():
    # Without this the settlement pass scores against {} and retires nothing,
    # however correct the pure logic is.
    wf = _workflow()
    for code in config.STATION_CODES:
        assert paths.github_path("settlements.jsonl", code) in wf, code


def test_trade_workflow_restores_settlements_before_running_the_trader():
    wf = _workflow()
    assert "origin data" in wf
    # Against the step NAME, not `python trader.py` — the header comment mentions
    # the command too, and matching that would compare against a line in the prose.
    assert wf.index("settlements.jsonl") < wf.index("Run one trade pass")


def _held_yesterday() -> dict:
    return {"entries": {"KXHIGHTDAL-26JUL30-B100.5": {
        "entry_ask": 0.44, "side": "yes", "count": 1, "variable": "high",
        "day": "2026-07-30", "floor": 100, "cap": 101, "label": "100° to 101°",
        "ts": "2026-07-30T15:51:10-05:00"}}}


def test_run_once_reports_past_day_positions_it_could_not_settle(capsys):
    """A past-day position still held after the settlement pass is an anomaly —
    the settlement lands the same evening, so by the next window it is on file.
    Silence is what let this run unnoticed for days."""
    d = Deps(state=_live_params(), snap=_high_snap(), contracts={}, book={},
             implied={}, runtime=_held_yesterday())
    trader.run_once(now=_NOW, deps=d)

    out = capsys.readouterr().out
    assert "KXHIGHTDAL-26JUL30-B100.5" in out
    assert "settlement" in out.lower()


def test_run_once_stays_quiet_when_nothing_is_overdue(capsys):
    d = Deps(state=_live_params(), snap=_high_snap(), contracts={}, book={},
             implied={}, runtime={"entries": {}})
    trader.run_once(now=_NOW, deps=d)
    assert "settlement" not in capsys.readouterr().out.lower()
