"""Journal page data layer: every settled day scored, newest first, with a
summary strip (7-day hit rates, total P&L, exact streak)."""
import sys
from datetime import date
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import journal_view

TODAY = date(2026, 7, 18)


def _rows(day_iso, high, low, flags=()):
    out = []
    for var, cons in (("high", high), ("low", low)):
        r = {"target_date": day_iso, "variable": var, "basis": "cli",
             "lead_bucket": 24, "consensus": cons}
        for f in flags:
            r[f] = True
        out.append(r)
    return out


def test_assemble_orders_newest_first_and_grades():
    settled = {date(2026, 7, 16): (93.0, 75.0), date(2026, 7, 17): (94.0, 77.0)}
    rows = _rows("2026-07-16", 93.2, 76.0) + _rows("2026-07-17", 94.0, 77.4)
    out = journal_view.assemble(TODAY, settled, rows)
    assert [d["date"] for d in out["days"]] == ["2026-07-17", "2026-07-16"]
    assert out["days"][0]["high"]["exact"] is True
    assert out["days"][1]["low"]["exact"] is False


def test_assemble_excludes_today_and_unforecast_days():
    settled = {TODAY: (95.0, 78.0), date(2026, 7, 1): (90.0, 74.0)}
    out = journal_view.assemble(TODAY, settled, [])   # no forecast rows at all
    assert out["days"] == []


def test_flags_collected_from_cli_rows():
    settled = {date(2026, 7, 16): (93.0, 75.0)}
    rows = _rows("2026-07-16", 93.0, 75.0, flags=("front_widened",))
    out = journal_view.assemble(TODAY, settled, rows)
    assert out["days"][0]["flags"] == ["front"]


def test_summary_hits_streak_and_pnl():
    settled = {date(2026, 7, 15): (92.0, 74.0),
               date(2026, 7, 16): (93.0, 75.0),
               date(2026, 7, 17): (94.0, 77.0)}
    rows = (_rows("2026-07-15", 92.0, 73.0)      # low miss -> breaks streak
            + _rows("2026-07-16", 93.0, 75.0)    # both exact
            + _rows("2026-07-17", 94.0, 77.0))   # both exact
    bets = [{"target_date": "2026-07-17", "status": "settled", "pnl": 10.0,
             "staked": 20.0},
            {"target_date": "2026-07-16", "status": "settled", "pnl": -4.0,
             "staked": 8.0}]
    out = journal_view.assemble(TODAY, settled, rows, bets)
    s = out["summary"]
    assert s["high_hits7"] == [3, 3]
    assert s["low_hits7"] == [2, 3]
    assert s["streak"] == 2                       # 7/17 and 7/16, broken 7/15
    assert s["pnl_total"] == 6.0
    assert out["days"][0]["pnl"]["net"] == 10.0


def test_assemble_empty_inputs():
    out = journal_view.assemble(TODAY, {}, [])
    assert out["days"] == [] and out["summary"]["streak"] == 0
