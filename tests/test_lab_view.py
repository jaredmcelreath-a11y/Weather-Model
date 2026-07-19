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


def test_per_model_scores_mae_and_bias():
    rows = [_row("2026-07-16", "high", 24, 92.0,
                 sources={"nws": 92.0, "mos_nbs": 94.0}),
            _row("2026-07-17", "high", 24, 94.0,
                 sources={"nws": 95.0, "mos_nbs": 94.0})]
    out = lab_view.per_model_scores(rows, SETTLED)
    assert out[("nws", "high", 24)] == {"n": 2, "mae": 1.0, "bias": 0.0}
    assert out[("mos_nbs", "high", 24)] == {"n": 2, "mae": 0.5, "bias": 0.5}


def test_per_model_scores_excludes_prefix_now_forward_same_day_lows():
    # Same-day lows logged by now-forward feeds (mos_lav/mos_nbs/nws/guidance)
    # before the 2026-07-19 covers_extreme fix were the wrong-tail bug
    # (14a2a3a) - they must not poison the scoreboard. Full-day feeds
    # (deterministic/ensemble) and day-ahead rows are unaffected.
    rows = [_row("2026-07-17", "low", 0, 77.0,
                 sources={"mos_lav": 84.0, "nws": 80.5, "guidance": 79.0,
                          "deterministic": 77.2}),
            _row("2026-07-17", "low", 24, 77.0, sources={"mos_lav": 78.0})]
    out = lab_view.per_model_scores(rows, SETTLED)
    for src in ("mos_lav", "nws", "guidance"):
        assert (src, "low", 0) not in out
    assert out[("deterministic", "low", 0)]["n"] == 1
    assert out[("mos_lav", "low", 24)]["n"] == 1


def test_per_model_scores_skips_cohort_rows():
    rows = [_row("2026-07-17", "high", 0, 94.0, sources={"nws": 94.0},
                 capture_cohort="0900")]
    assert lab_view.per_model_scores(rows, SETTLED) == {}


def test_chart_frame_long_form():
    h2h = {("high", 24): {"n": 1, "prod_mae": 1.0, "cand_mae": 0.5,
                          "prod_wins": 0, "cand_wins": 1, "ties": 0,
                          "days": [{"date": "2026-07-16", "prod_err": 1.0,
                                    "cand_err": 0.5}]}}
    recs = lab_view.chart_frame(h2h)
    assert {r["series"] for r in recs} == {"Production", "Candidate"}
    assert all(r["variable"] == "high" and r["lead"] == 24 for r in recs)
    assert recs[0]["date"] == "2026-07-16"


def test_render_smoke_empty_and_full():
    lab_view.render(lambda: ({}, {}))
    h2h = {("high", 24): {"n": 2, "prod_mae": 0.5, "cand_mae": 1.25,
                          "prod_wins": 1, "cand_wins": 1, "ties": 0,
                          "days": [{"date": "2026-07-16", "prod_err": 1.0,
                                    "cand_err": 0.5},
                                   {"date": "2026-07-17", "prod_err": 0.0,
                                    "cand_err": 2.0}]}}
    models = {("nws", "high", 24): {"n": 2, "mae": 1.0, "bias": 0.0},
              ("mos_nbs", "low", 0): {"n": 3, "mae": 0.4, "bias": -0.1}}
    lab_view.render(lambda: (h2h, models))


def test_error_chart_ships_datetimes_not_bare_date_strings():
    # A bare "2026-07-18" in a temporal encoding is parsed as UTC midnight by
    # the browser and rendered in local time — the point lands on July 17 for
    # US viewers. The chart must ship naive datetimes ("...T00:00:00"), which
    # parse as LOCAL midnight and stay on the right day.
    recs = [{"date": "2026-07-18", "variable": "low", "lead": 0,
             "series": s, "abs_err": 1.0} for s in ("Production", "Candidate")]
    spec = lab_view._error_chart(recs).to_dict()
    for ds in spec["datasets"].values():
        for row in ds:
            assert "T" in str(row["date"]), row


def test_shadow_expander_lives_in_lab_titlecased_no_emoji(monkeypatch):
    # Moved off the Forecast page (2026-07-19): the live shadow-vs-production
    # comparison belongs with the Lab's scored head-to-head. Title Case, no emoji.
    import shadow
    rows = [{"day": "2026-07-19", "variable": "high",
             "production": 96.0, "candidate": 95.5, "gap": -0.5}]
    monkeypatch.setattr(shadow, "consensus_comparison", lambda snap: rows)
    fake_st = MagicMock()
    monkeypatch.setattr(lab_view, "st", fake_st)
    lab_view._render_shadow_comparison({"today": {}})
    fake_st.expander.assert_called_once_with(
        "Candidate Model Set (Shadow) — Not Live")
