from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import os

import betting_log
import solar
from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def _at(h, m):
    return datetime(2026, 7, 3, h, m, tzinfo=_TZ)


def test_current_slot_exact_match():
    assert betting_log.current_slot(_at(15, 30)) == "15:30"


def test_current_slot_within_tolerance():
    assert betting_log.current_slot(_at(15, 4)) == "15:00"    # +4 min
    assert betting_log.current_slot(_at(16, 24)) == "16:30"   # -6 min


def test_current_slot_eight_minute_boundary_catches():
    # ±8 tolerance: an 8-min offset (the cron's :52/:07-style boundary run) fires.
    assert betting_log.current_slot(_at(14, 52)) == "15:00"   # :52 run covers the :00 slot
    assert betting_log.current_slot(_at(15, 8)) == "15:00"    # +8 min, inclusive boundary


def test_current_slot_outside_tolerance_is_none():
    assert betting_log.current_slot(_at(15, 9)) is None       # 9 min > 8 -> just outside
    assert betting_log.current_slot(_at(15, 12)) is None      # 12 min off any slot


def test_current_slot_slot_sets_defined():
    # Morning low slots are sunrise-anchored (symbolic labels); afternoon high
    # slots are fixed clock times.
    assert [lbl for lbl, _off in betting_log.LOW_SLOT_OFFSETS] == \
        ["sr-90", "sr-60", "sr-30", "sr", "sr+30"]
    assert betting_log.HIGH_SLOTS == ["15:00", "15:30", "16:00", "16:30", "17:00"]
    assert betting_log.SLOTS == \
        ["sr-90", "sr-60", "sr-30", "sr", "sr+30",
         "15:00", "15:30", "16:00", "16:30", "17:00"]
    assert betting_log.SLOT_TOLERANCE_MIN == 8


def test_current_slot_matches_sunrise_anchored_low_slots():
    day = date(2026, 7, 3)
    sr = solar.sunrise(day)
    assert betting_log.current_slot(sr) == "sr"                      # at sunrise
    assert betting_log.current_slot(sr - timedelta(minutes=30)) == "sr-30"
    assert betting_log.current_slot(sr - timedelta(minutes=90)) == "sr-90"
    assert betting_log.current_slot(sr + timedelta(minutes=30)) == "sr+30"
    assert betting_log.current_slot(sr + timedelta(minutes=5)) == "sr"   # +5 within ±8
    assert betting_log.current_slot(sr + timedelta(minutes=60)) is None  # between anchors


def test_morning_window_tracks_the_season():
    # The 'sr' slot resolves to a later wall-clock time in winter than summer,
    # so the morning window follows the trough across the year.
    summer = solar.sunrise(date(2026, 7, 3))
    winter = solar.sunrise(date(2026, 1, 3))
    assert (winter.hour, winter.minute) > (summer.hour, summer.minute)
    assert betting_log.current_slot(summer) == "sr"
    assert betting_log.current_slot(winter) == "sr"


_CLI = {
    "today": {
        "day": "2026-07-03",
        "high": {"consensus": 97.9, "probabilities": {"97": 0.4, "98": 0.35, "96": 0.15, "99": 0.1},
                 "observed_so_far": 91.94, "observed_continuous": 93.2,
                 "peak_locked": False, "sigma_used": 1.1},
        "low": {"consensus": 78.0, "probabilities": {"78": 0.5, "77": 0.3, "79": 0.2},
                "observed_so_far": 79.0, "observed_continuous": 79.0,
                "peak_locked": True, "sigma_used": 0.8},
    },
    "market": {"today": {
        "high": {"ev": 96.9, "buckets": [[None, 96, 0.3], [97, 98, 0.6], [99, 100, 0.1]], "volume": 5000.0},
        "low": {"ev": 78.1, "buckets": [[77, 78, 0.7], [79, 80, 0.3]], "volume": 500.0},
    }},
}
_HOURLY = {"today": {"day": "2026-07-03",
                     "high": {"consensus": 97.0}, "low": {"consensus": 78.0}}}
_CALIB = {"settlement_offset": {"high": 0.89, "high_std": 0.77, "low": -0.33, "low_std": 0.47}}


def test_record_afternoon_slot_high_only(tmp_path):
    # Afternoon slots capture the high (the low bottomed out at dawn and is
    # settled by now, so no low row is written).
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)
    rows = betting_log.load(p)
    assert {r["variable"] for r in rows} == {"high"}
    hi = next(r for r in rows if r["variable"] == "high")
    assert hi["capture_slot"] == "15:30"
    assert hi["target_date"] == "2026-07-03"
    assert hi["cli_consensus"] == 97.9
    assert hi["hourly_consensus"] == 97.0
    assert hi["flat_offset"] == 0.89
    assert round(hi["live_gap"], 2) == 1.26        # 93.2 - 91.94
    assert hi["peak_locked"] is False
    assert hi["market_ev"] == 96.9
    assert hi["model_bins"][0] == ["97", 0.4]      # top model bin
    assert hi["market_buckets"][1] == [97, 98, 0.6]


def test_record_morning_slot_low_only(tmp_path):
    # Morning slots capture the low as it bottoms out near the sunrise trough;
    # the high is ~10h away (a day-ahead forecast, not a betting-time number), so
    # no high row is written.
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "sr", _CALIB, path=p)
    rows = betting_log.load(p)
    assert {r["variable"] for r in rows} == {"low"}
    lo = rows[0]
    assert lo["capture_slot"] == "sr"
    assert lo["target_date"] == "2026-07-03"
    assert lo["cli_consensus"] == 78.0
    assert lo["hourly_consensus"] == 78.0
    assert lo["flat_offset"] == -0.33
    assert lo["peak_locked"] is True
    assert lo["market_ev"] == 78.1
    assert lo["model_bins"][0] == ["78", 0.5]      # top model bin
    assert lo["market_buckets"][0] == [77, 78, 0.7]


def test_record_upserts_same_slot(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)   # same slot again
    rows = [r for r in betting_log.load(p) if r["variable"] == "high"]
    assert len(rows) == 1                                        # overwritten, not appended


def test_record_distinct_slots_both_persist(tmp_path):
    p = str(tmp_path / "b.jsonl")
    betting_log.record(_CLI, _HOURLY, "15:00", _CALIB, path=p)
    betting_log.record(_CLI, _HOURLY, "15:30", _CALIB, path=p)
    slots = sorted(r["capture_slot"] for r in betting_log.load(p) if r["variable"] == "high")
    assert slots == ["15:00", "15:30"]


def test_record_market_absent_is_omitted(tmp_path):
    p = str(tmp_path / "b.jsonl")
    cli_no_market = {"today": _CLI["today"]}                     # no "market" key
    betting_log.record(cli_no_market, _HOURLY, "16:00", _CALIB, path=p)
    hi = next(r for r in betting_log.load(p) if r["variable"] == "high")
    assert "market_ev" not in hi and "market_buckets" not in hi


def test_row_carries_regime_flags():
    cli_var = {"consensus": 78.0, "probabilities": {"78": 1.0},
               "observed_so_far": 78.0, "observed_continuous": None,
               "peak_locked": True, "sigma_used": 0.7,
               "convective_widened": True, "front_widened": False}
    rec = betting_log._row("2026-07-13", "low", "15:00", cli_var, {}, None,
                           -0.36, "2026-07-13T15:00:00-05:00")
    assert rec["convective_widened"] is True
    assert rec["front_widened"] is False


def test_row_flags_default_false_when_absent():
    # A prediction dict from before the flags existed must not crash and must
    # read as un-flagged (explicit False in betting rows, for the join analysis).
    cli_var = {"consensus": 97.0, "probabilities": {"97": 1.0},
               "observed_so_far": None, "observed_continuous": None,
               "peak_locked": False, "sigma_used": 1.0}
    rec = betting_log._row("2026-07-13", "high", "15:00", cli_var, {}, None,
                           0.91, "2026-07-13T15:00:00-05:00")
    assert rec["convective_widened"] is False
    assert rec["front_widened"] is False


def test_row_logs_market_volume():
    cli_var = {"consensus": 97.5, "probabilities": {"97": 0.6, "98": 0.4}}
    market_var = {"ev": 97.2, "buckets": [[97, 98, 1.0]], "volume": 42.0}
    rec = betting_log._row("2026-07-13", "high", "15:30", cli_var, {}, market_var,
                           0.89, "2026-07-13T15:30:00-05:00")
    assert rec["market_ev"] == 97.2
    assert rec["market_volume"] == 42.0


def test_row_without_market_has_no_volume_key():
    cli_var = {"consensus": 97.5, "probabilities": {"97": 0.6, "98": 0.4}}
    rec = betting_log._row("2026-07-13", "high", "15:30", cli_var, {}, None,
                           0.89, "2026-07-13T15:30:00-05:00")
    assert "market_volume" not in rec


# ---------------------------------------------------------------------------
# GitHub dual-read (cloud deploy): load() with no explicit path must read the
# remote data-branch file when the dashboard has one configured, mirroring
# settlements.load — otherwise the deployed Edge page sees zero rows forever.

_REMOTE = [{"target_date": "2026-07-10", "variable": "high",
            "capture_slot": "15:00"}]


def test_load_reads_github_when_configured(monkeypatch):
    monkeypatch.setenv("FORECAST_LOG_GH_REPO", "someone/weather")
    seen = {}

    def fake_load(cfg):
        seen.update(cfg)
        return list(_REMOTE)

    monkeypatch.setattr(betting_log, "_load_github", fake_load)
    assert betting_log.load() == _REMOTE
    assert seen["repo"] == "someone/weather"
    assert seen["path"] == "betting_log.jsonl"


def test_load_explicit_path_ignores_github(tmp_path, monkeypatch):
    # record() and the Action always pass a path — they must keep reading the
    # local file even when the remote config is present in the environment.
    monkeypatch.setenv("FORECAST_LOG_GH_REPO", "someone/weather")
    monkeypatch.setattr(betting_log, "_load_github",
                        lambda cfg: (_ for _ in ()).throw(AssertionError("remote hit")))
    p = tmp_path / "betting_log.jsonl"
    p.write_text('{"target_date": "2026-07-11", "variable": "low", '
                 '"capture_slot": "sr"}\n')
    rows = betting_log.load(str(p))
    assert rows == [{"target_date": "2026-07-11", "variable": "low",
                     "capture_slot": "sr"}]


def test_load_without_config_reads_local(tmp_path, monkeypatch):
    monkeypatch.delenv("FORECAST_LOG_GH_REPO", raising=False)
    p = tmp_path / "betting_log.jsonl"
    p.write_text('{"target_date": "2026-07-12", "variable": "high", '
                 '"capture_slot": "16:00"}\n')
    assert betting_log.load(str(p))[0]["target_date"] == "2026-07-12"
