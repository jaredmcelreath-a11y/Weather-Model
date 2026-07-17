"""Fixed 09:00 same-day capture cohort — an honest decision-time same-day number.

The rolling same-day (lead-0) row is upserted all day, so the survivor is the
~11:45pm capture when the day is already settled; scoring it overstates decision-
time skill. A separately-keyed 09:00 capture persists so the same-day accuracy
reflects a real morning forecast (high ~6-7h out, low near its trough).
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import forecast_log
import scoring
from config import TIMEZONE
from sources import station_history

TZ = ZoneInfo(TIMEZONE)
TODAY = date(2026, 6, 16)


def _snapshot(now):
    return {
        "updated": now.isoformat(),
        "today": {"day": TODAY.isoformat(),
                  "high": {"consensus": 95, "probabilities": {"95": 0.5, "96": 0.5}},
                  "low": {"consensus": 77, "probabilities": {"77": 1.0}}},
        "tomorrow": {"day": (TODAY + timedelta(days=1)).isoformat(),
                     "high": {"consensus": 96, "probabilities": {"96": 1.0}},
                     "low": {"consensus": 78, "probabilities": {"78": 1.0}}},
    }


def test_morning_cohort_window():
    assert forecast_log.morning_cohort(datetime(2026, 6, 16, 9, 3, tzinfo=TZ)) == "0900"
    assert forecast_log.morning_cohort(datetime(2026, 6, 16, 8, 54, tzinfo=TZ)) == "0900"
    assert forecast_log.morning_cohort(datetime(2026, 6, 16, 9, 12, tzinfo=TZ)) is None
    assert forecast_log.morning_cohort(datetime(2026, 6, 16, 22, 0, tzinfo=TZ)) is None


def test_record_writes_0900_cohort_for_today_only(tmp_path):
    p = str(tmp_path / "log.jsonl")
    forecast_log.record(_snapshot(datetime(2026, 6, 16, 9, 2, tzinfo=TZ)), path=p)
    rows = forecast_log.load(p)
    cohort = [r for r in rows if r.get("capture_cohort") == "0900"]
    assert {r["variable"] for r in cohort} == {"high", "low"}
    assert all(r["target_date"] == TODAY.isoformat() for r in cohort)   # today only
    rolling = [r for r in rows if r.get("capture_cohort") is None]
    assert len(rolling) == 4                                            # today+tomorrow x hi+lo


def test_no_cohort_outside_window(tmp_path):
    p = str(tmp_path / "log.jsonl")
    forecast_log.record(_snapshot(datetime(2026, 6, 16, 22, 0, tzinfo=TZ)), path=p)
    assert [r for r in forecast_log.load(p) if r.get("capture_cohort")] == []


def test_0900_cohort_survives_evening_upsert(tmp_path):
    p = str(tmp_path / "log.jsonl")
    forecast_log.record(_snapshot(datetime(2026, 6, 16, 9, 0, tzinfo=TZ)), path=p)
    forecast_log.record(_snapshot(datetime(2026, 6, 16, 23, 45, tzinfo=TZ)), path=p)
    cohort = [r for r in forecast_log.load(p) if r.get("capture_cohort") == "0900"]
    assert len(cohort) == 2                                             # not upserted away


def test_settled_records_cohort_filter(tmp_path, monkeypatch):
    p = str(tmp_path / "log.jsonl")
    forecast_log.record(_snapshot(datetime(2026, 6, 16, 9, 0, tzinfo=TZ)), path=p)
    monkeypatch.setattr(forecast_log, "_PATH", p)
    rolling = scoring._settled_records(today=date(2026, 6, 18))
    cohort = scoring._settled_records(today=date(2026, 6, 18), cohort="0900")
    assert rolling and all(r.get("capture_cohort") is None for r in rolling)
    assert cohort and all(r.get("capture_cohort") == "0900" for r in cohort)


def test_score_reports_same_day_0900_and_keeps_it_out_of_by_lead(tmp_path, monkeypatch):
    p = str(tmp_path / "log.jsonl")
    forecast_log.record(_snapshot(datetime(2026, 6, 16, 9, 0, tzinfo=TZ)), path=p)
    monkeypatch.setattr(forecast_log, "_PATH", p)
    monkeypatch.setattr(station_history, "fetch_actual",
                        lambda s, e: {TODAY: (95.0, 77.0)})
    res = scoring.score(today=date(2026, 6, 18))
    sd = res["same_day_0900"]
    assert sd["high"]["n"] == 1 and sd["high"]["exact_peak"] == 100
    assert sd["low"]["n"] == 1 and sd["low"]["exact_peak"] == 100
    # the cohort rows must NOT inflate the rolling bucket-0 counts
    assert res["by_lead"][0]["high"]["n"] == 1                          # rolling only


def test_score_empty_log_reports_empty_cohort(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast_log, "_PATH", str(tmp_path / "none.jsonl"))
    assert scoring.score(today=TODAY)["same_day_0900"] == {}
