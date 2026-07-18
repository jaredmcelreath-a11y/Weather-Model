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
    key = ("15:30", "high", "all")       # pooled subset
    assert m[key]["n"] == 2
    assert round(m[key]["model_mae"], 2) == 0.05       # |98-98|, |95.9-96| -> (0+0.1)/2
    assert round(m[key]["market_mae"], 2) == 0.60      # (1.1+0.1)/2
    # Q2: live_gap (1.2, 0.8) vs flat (0.89) predicting actual_gap (1.0, 1.0)
    # flat rmse = sqrt(((0.89-1)^2)*2/2)=0.11 ; live rmse = sqrt((0.2^2+0.2^2)/2)=0.2
    assert round(m[key]["flat_rmse"], 2) == 0.11
    assert round(m[key]["live_rmse"], 2) == 0.20


def test_run_end_to_end(tmp_path, monkeypatch):
    rows = [_hi("15:30", 98.0, 96.9, [[None, 96, 0.2], [97, 98, 0.8]], 98.0, 97.0, 1.2)]
    # settlement maps come in via join inside run(); monkeypatch as_map through a shim
    monkeypatch.setattr(edge_report, "_settlement_maps",
                        lambda: ({date(2026, 7, 1): (98.0, 79.0)},
                                 {date(2026, 7, 1): (97.0, 79.0)}))
    rows[0]["target_date"] = "2026-07-01"
    out = str(tmp_path / "edge")
    paths = edge_report.run(rows, out)
    assert any(p.endswith("metrics.csv") for p in paths)


def test_write_report_creates_files(tmp_path):
    m = {("15:30", "high", "all"): {"n": 5, "model_mae": 0.5, "market_mae": 0.6,
          "disagreements": 2, "model_bin_wins": 1, "market_bin_wins": 1,
          "n_boundary": 3, "flat_rmse": 0.75, "live_rmse": 0.4,
          "flip_toward": 2, "flip_away": 0}}
    out = str(tmp_path / "edge")
    paths = edge_report.write_report(m, out)
    assert os.path.exists(os.path.join(out, "metrics.csv"))
    assert os.path.exists(os.path.join(out, "ASSESSMENT.md"))
    body = open(os.path.join(out, "metrics.csv")).read()
    assert "15:30" in body and "0.4" in body


def test_metrics_slices_by_boundary():
    # One (slot, variable) group with a boundary row (cli 98.0 -> 0.5 from 98.5)
    # and a mid-bin row (cli 95.5 -> 1.0 from the nearest edge). The decision gate
    # is about boundary days, so metrics must slice Q1/Q2, not just count them.
    joined = [
        _hi("16:00", 98.0, 96.9, [[None, 96, 0.2], [97, 98, 0.8]], 98.0, 97.0, 1.2),
        _hi("16:00", 95.5, 95.4, [[95, 96, 0.9], [97, 98, 0.1]], 95.0, 94.0, 0.9),
    ]
    m = edge_report.metrics(joined)
    assert ("16:00", "high", "all") in m
    assert ("16:00", "high", "boundary") in m
    assert ("16:00", "high", "mid_bin") in m

    b = m[("16:00", "high", "boundary")]
    mid = m[("16:00", "high", "mid_bin")]
    assert b["n"] == 1 and mid["n"] == 1
    # boundary row: model err |98-98| = 0
    assert b["model_mae"] == 0.0
    # boundary Q2: flat 0.89 vs actual 1.0 -> 0.11 ; live 1.2 vs 1.0 -> 0.20
    assert round(b["flat_rmse"], 2) == 0.11
    assert round(b["live_rmse"], 2) == 0.20
    # mid-bin row: model err |95.5-95.0| = 0.5
    assert mid["model_mae"] == 0.5
    # the "all" subset still pools both rows
    assert m[("16:00", "high", "all")]["n"] == 2


def test_metrics_values_are_rounded():
    # errors 0.7 and 0.1 average to 0.4, which naive float math renders as
    # 0.39999999999999997 — the report must round so the artifact is readable.
    joined = [
        _hi("16:30", 95.7, 95.0, [[95, 96, 1.0]], 95.0, 94.0, 1.0),   # err 0.7
        _hi("16:30", 95.1, 95.0, [[95, 96, 1.0]], 95.0, 94.0, 1.0),   # err 0.1
    ]
    m = edge_report.metrics(joined)[("16:30", "high", "all")]
    assert m["model_mae"] == 0.4          # not 0.39999999999999997


def test_metrics_market_bucket_uses_ev_not_mode():
    # Market's MODE is (95,96) (p=0.5), but its MEAN (ev 97.5) lands in (97,98),
    # which is the settled bucket. Under the fair mean-vs-mean rule the market
    # DISAGREES with the model (95,96) and WINS. The old mode rule would have put
    # market_b == model_b == (95,96) and counted no disagreement at all.
    joined = [
        _hi("15:30", 95.5, 97.5, [[95, 96, 0.5], [97, 98, 0.3], [99, 100, 0.2]],
            98.0, 97.0, 1.0),
    ]
    m = edge_report.metrics(joined)[("15:30", "high", "all")]
    assert m["disagreements"] == 1
    assert m["market_bin_wins"] == 1
    assert m["model_bin_wins"] == 0


def test_metrics_skips_row_with_no_market_ev():
    # A row with market_buckets but market_ev None must not blow up settled_bucket;
    # it is skipped from the disagreement tally (n still counts it).
    row = _hi("15:30", 95.5, None, [[95, 96, 1.0]], 95.0, 94.0, 1.0)
    m = edge_report.metrics([row])[("15:30", "high", "all")]
    assert m["n"] == 1
    assert m["disagreements"] == 0
