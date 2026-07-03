import edge_report
from datetime import date
import os

_BUCKETS = [[None, 96, 0.3], [97, 98, 0.6], [99, 100, 0.1]]

_ROWS = [
    {"target_date": "2026-07-01", "variable": "high", "capture_slot": "15:30",
     "cli_consensus": 97.9, "flat_offset": 0.89, "live_gap": 1.2},
    {"target_date": "2026-07-09", "variable": "high", "capture_slot": "15:30",  # unsettled
     "cli_consensus": 99.0, "flat_offset": 0.89, "live_gap": 0.5},
]
_CLI_MAP = {date(2026, 7, 1): (98.0, 79.0)}
_HOURLY_MAP = {date(2026, 7, 1): (97.0, 79.0)}


def test_settled_bucket_closed_range():
    assert edge_report.settled_bucket(97.0, _BUCKETS) == (97, 98)
    assert edge_report.settled_bucket(98.0, _BUCKETS) == (97, 98)


def test_settled_bucket_open_low_end():
    assert edge_report.settled_bucket(95.0, _BUCKETS) == (None, 96)


def test_settled_bucket_miss_returns_none():
    assert edge_report.settled_bucket(105.0, _BUCKETS) is None


def test_top_bucket():
    assert edge_report.top_bucket(_BUCKETS) == (97, 98)


def test_is_boundary():
    # Kalshi even|odd edges sit at even+0.5 (...94.5, 96.5, 98.5...).
    assert edge_report.is_boundary(96.5) is True          # on the 96|97 edge, dist 0
    assert edge_report.is_boundary(97.0) is True          # 0.5 from 96.5
    assert edge_report.is_boundary(95.4) is False         # 1.1 from 96.5
    assert edge_report.is_boundary(97.6) is False         # 1.1 from 96.5 and 98.5


def test_join_attaches_settlement_and_gap():
    out = edge_report.join(_ROWS, _CLI_MAP, _HOURLY_MAP)
    assert len(out) == 1                                  # unsettled row dropped
    r = out[0]
    assert r["settled_cli"] == 98.0
    assert r["settled_hourly"] == 97.0
    assert r["actual_gap"] == 1.0


def _hi(slot, cli, mkt_ev, buckets, settled_cli, settled_hourly, live_gap, flat=0.89,
        top_model=None):
    return {"capture_slot": slot, "variable": "high", "cli_consensus": cli,
            "market_ev": mkt_ev, "market_buckets": buckets,
            "model_bins": top_model or [["%d" % round(cli), 1.0]],
            "settled_cli": settled_cli, "settled_hourly": settled_hourly,
            "actual_gap": settled_cli - settled_hourly, "live_gap": live_gap,
            "flat_offset": flat}


def test_metrics_mae_and_offset():
    joined = [
        # model says 98 (right), market EV 96.9 (off by 1.1); settled 98/hourly 97, gap 1.0
        _hi("15:30", 98.0, 96.9, [[None, 96, 0.2], [97, 98, 0.8]], 98.0, 97.0, 1.2),
        # model 95.9 (off 0.1), market 96.1 (off 0.1); settled 96/hourly 95, gap 1.0
        _hi("15:30", 95.9, 96.1, [[95, 96, 0.9], [97, 98, 0.1]], 96.0, 95.0, 0.8),
    ]
    m = edge_report.metrics(joined)
    key = ("15:30", "high")
    assert m[key]["n"] == 2
    assert round(m[key]["model_mae"], 2) == 0.05       # |98-98|, |95.9-96| -> (0+0.1)/2
    assert round(m[key]["market_mae"], 2) == 0.60      # (1.1+0.1)/2
    # Q2: live_gap (1.2, 0.8) vs flat (0.89) predicting actual_gap (1.0, 1.0)
    # flat rmse = sqrt(((0.89-1)^2)*2/2)=0.11 ; live rmse = sqrt((0.2^2+0.2^2)/2)=0.2
    assert round(m[key]["flat_rmse"], 2) == 0.11
    assert round(m[key]["live_rmse"], 2) == 0.20


def test_write_report_creates_files(tmp_path):
    m = {("15:30", "high"): {"n": 5, "model_mae": 0.5, "market_mae": 0.6,
          "disagreements": 2, "model_bin_wins": 1, "market_bin_wins": 1,
          "n_boundary": 3, "flat_rmse": 0.75, "live_rmse": 0.4,
          "flip_toward": 2, "flip_away": 0}}
    out = str(tmp_path / "edge")
    paths = edge_report.write_report(m, out)
    assert os.path.exists(os.path.join(out, "metrics.csv"))
    assert os.path.exists(os.path.join(out, "ASSESSMENT.md"))
    body = open(os.path.join(out, "metrics.csv")).read()
    assert "15:30" in body and "0.4" in body
