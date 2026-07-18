"""Pure data for the Morning Recap card: today's setup (from the snapshot) and
yesterday's scorecard (settlements joined to the model's logged forecast)."""
from datetime import date

import recap

_SNAP = {"today": {"day": "2026-07-18",
                   "high": {"consensus": 99.2, "peak_locked": False,
                            "probabilities": {"98": 0.30, "99": 0.41, "100": 0.29}},
                   "low": {"consensus": 78.0, "observed_so_far": 78.0,
                           "peak_locked": True, "probabilities": {"78": 1.0}}}}


def test_today_setup_reads_snapshot_and_market():
    out = recap.today_setup(_SNAP, mkt_high=98.9, mkt_low=78.1)
    assert out["date"] == "2026-07-18"
    assert out["high"]["consensus"] == 99.2
    assert out["high"]["top_bin"] == ["99", 0.41]
    assert out["high"]["market_ev"] == 98.9
    assert out["high"]["locked"] is False
    assert out["low"]["observed"] == 78.0
    assert out["low"]["locked"] is True
    assert out["low"]["market_ev"] == 78.1


def test_today_setup_market_optional():
    out = recap.today_setup(_SNAP)
    assert out["high"]["market_ev"] is None and out["low"]["market_ev"] is None


_ROWS = [
    {"target_date": "2026-07-17", "variable": "high", "basis": "cli",
     "lead_bucket": 0, "capture_cohort": "0900", "consensus": 99.0,
     "probabilities": {"99": 1.0}, "market": {"ev": 98.5}},
    {"target_date": "2026-07-17", "variable": "low", "basis": "cli",
     "lead_bucket": 0, "capture_cohort": "0900", "consensus": 77.0,
     "probabilities": {"77": 1.0}},
]
_SETTLED = {date(2026, 7, 17): (100.0, 77.0)}


def test_yesterday_scorecard_grades_the_9am_cohort():
    out = recap.yesterday_scorecard(date(2026, 7, 18), _SETTLED, _ROWS)
    assert out["date"] == "2026-07-17"
    assert out["high"]["settled"] == 100.0 and out["high"]["model"] == 99.0
    assert out["high"]["exact"] is False and out["high"]["diff"] == -1.0
    assert out["high"]["market_closer"] is False   # |98.5-100|=1.5 > |99-100|=1.0
    assert out["low"]["exact"] is True
    assert out["low"]["market_closer"] is None      # no market logged


def test_yesterday_scorecard_none_when_unsettled():
    assert recap.yesterday_scorecard(date(2026, 7, 18), {}, _ROWS) is None


def test_yesterday_scorecard_prefers_9am_over_day_ahead():
    rows = [
        {"target_date": "2026-07-17", "variable": "high", "basis": "cli",
         "lead_bucket": 24, "consensus": 97.0, "probabilities": {"97": 1.0}},
        {"target_date": "2026-07-17", "variable": "high", "basis": "cli",
         "lead_bucket": 0, "capture_cohort": "0900", "consensus": 99.0,
         "probabilities": {"99": 1.0}},
    ]
    out = recap.yesterday_scorecard(date(2026, 7, 18), _SETTLED, rows)
    assert out["high"]["model"] == 99.0             # cohort wins over day-ahead


def test_yesterday_scorecard_falls_back_to_day_ahead():
    rows = [{"target_date": "2026-07-17", "variable": "low", "basis": "cli",
             "lead_bucket": 24, "consensus": 76.0, "probabilities": {"76": 1.0}}]
    out = recap.yesterday_scorecard(date(2026, 7, 18), _SETTLED, rows)
    assert out["low"]["model"] == 76.0 and "high" not in out


_BETS = [
    {"target_date": "2026-07-17", "status": "settled", "pnl": 30.0, "staked": 50.0},
    {"target_date": "2026-07-17", "status": "closed", "pnl": 12.0, "staked": 40.0},
    {"target_date": "2026-07-17", "status": "settled", "pnl": -8.0, "staked": 20.0},
    {"target_date": "2026-07-16", "status": "settled", "pnl": 100.0, "staked": 10.0},
    {"target_date": "2026-07-17", "status": "open", "pnl": None, "staked": 15.0},
]


def test_yesterday_pnl_sums_realized_for_that_weather_day():
    out = recap.yesterday_pnl("2026-07-17", _BETS)
    assert out["net"] == 34.0                       # 30 + 12 - 8 (open row excluded)
    assert out["n"] == 3 and out["wins"] == 2 and out["losses"] == 1
    assert out["pct"] == round(100 * 34.0 / 110.0, 1)   # staked 50+40+20


def test_yesterday_pnl_none_when_no_settled_bets():
    assert recap.yesterday_pnl("2026-07-15", _BETS) is None


def test_yesterday_scorecard_attaches_pnl():
    out = recap.yesterday_scorecard(date(2026, 7, 18), _SETTLED, _ROWS, bet_rows=_BETS)
    assert out["pnl"]["net"] == 34.0 and out["pnl"]["n"] == 3


def test_day_scorecard_grades_arbitrary_day():
    # Same grading as yesterday_scorecard, but for any settled day.
    from datetime import date
    import recap
    day = date(2026, 7, 10)
    settled = {day: (94.0, 77.0)}
    rows = [{"target_date": "2026-07-10", "variable": "high", "basis": "cli",
             "lead_bucket": 24, "consensus": 93.0},
            {"target_date": "2026-07-10", "variable": "low", "basis": "cli",
             "lead_bucket": 24, "consensus": 77.4}]
    out = recap.day_scorecard(day, settled, rows)
    assert out["date"] == "2026-07-10"
    assert out["high"]["settled"] == 94.0 and out["high"]["exact"] is False
    assert out["low"]["exact"] is True


def test_day_scorecard_none_when_unsettled():
    from datetime import date
    import recap
    assert recap.day_scorecard(date(2026, 7, 10), {}, []) is None
