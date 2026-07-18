"""Lab page data layer: shadow-consensus head-to-head + per-model scoreboard,
both scored against CLI settlements."""
import sys
from datetime import date
from unittest.mock import MagicMock

try:
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

import lab_view

SETTLED = {date(2026, 7, 16): (93.0, 75.0), date(2026, 7, 17): (94.0, 77.0)}


def _row(day_iso, var, lead, cons, cand=None, **extra):
    r = {"target_date": day_iso, "variable": var, "basis": "cli",
         "lead_bucket": lead, "consensus": cons}
    if cand is not None:
        r["candidate_consensus"] = cand
    r.update(extra)
    return r


def test_head_to_head_scores_and_wins():
    rows = [_row("2026-07-16", "high", 24, 92.0, cand=93.5),   # prod 1.0 cand 0.5
            _row("2026-07-17", "high", 24, 94.0, cand=92.0),   # prod 0.0 cand 2.0
            _row("2026-07-17", "low", 24, 77.4, cand=77.4)]    # tie
    out = lab_view.head_to_head(rows, SETTLED)
    g = out[("high", 24)]
    assert g["n"] == 2
    assert g["prod_mae"] == 0.5 and g["cand_mae"] == 1.25
    assert g["prod_wins"] == 1 and g["cand_wins"] == 1
    assert out[("low", 24)]["ties"] == 1
    assert g["days"][0]["date"] == "2026-07-16"


def test_head_to_head_skips_cohort_unsettled_and_candidateless():
    rows = [_row("2026-07-17", "high", 0, 94.0, cand=93.0, capture_cohort="0900"),
            _row("2026-07-18", "high", 24, 95.0, cand=94.0),   # unsettled
            _row("2026-07-17", "high", 24, 94.0)]              # no candidate
    assert lab_view.head_to_head(rows, SETTLED) == {}
