"""Unit tests for the model-at-bet-time reconstruction: nearest-snapshot pick,
normal-CDF probability over a contract range, edge sign, and the '—' gap."""

from datetime import datetime, timezone

import bet_history as bh


def _bet_row(fill_hour, side="yes", entry=0.42):
    return {"ticker": "KXHIGHTDAL-26JUN22-B97", "variable": "high",
            "floor": 97, "cap": 98, "strike_type": "between", "side": side,
            "entry": entry, "first_ts": datetime(2026, 6, 22, fill_hour, tzinfo=timezone.utc),
            "status": "settled"}


BETTING = [{"target_date": "2026-06-22", "variable": "high",
            "captured_at": "2026-06-22T19:45:00+00:00",
            "cli_consensus": 97.5, "sigma_used": 1.0}]


def test_model_at_bet_uses_nearest_betting_snapshot():
    p, edge, agree = bh.model_at_bet(
        datetime(2026, 6, 22, 19, 47, tzinfo=timezone.utc),
        "high", 97, 98, "between", "yes", 0.42, BETTING, [], calib={})
    # N(97.5, 1.0) over [96.5, 98.5] ~ 0.68; yes side; edge = 0.68 - 0.42 > 0
    assert 0.60 < p < 0.75
    assert edge > 0 and agree is True


def test_no_snapshot_within_tolerance_returns_none():
    p, edge, agree = bh.model_at_bet(
        datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc),  # hours from the 19:45 snap
        "high", 97, 98, "between", "yes", 0.42, BETTING, [], calib={}, tol_min=45)
    assert (p, edge, agree) == (None, None, None)


def test_falls_back_to_consensus_history_with_calib_sigma():
    consensus = [{"target_date": "2026-06-22", "variable": "high", "basis": "cli",
                  "captured_at": "2026-06-22T14:05:00+00:00", "consensus": 99.0}]
    p, edge, agree = bh.model_at_bet(
        datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc),
        "high", 97, 98, "between", "no", 0.55, [], consensus,
        calib={"sigma": {"high": 2.0}})
    # consensus 99 well above [97,98] -> low yes prob -> high NO prob -> positive edge
    assert p is not None and agree is True


def test_annotate_rows_sets_model_fields():
    rows = [_bet_row(19)]
    bh.annotate_rows(rows, BETTING, [], calib={})
    assert rows[0]["model_prob"] is not None
    assert rows[0]["agree"] in (True, False)


CONSENSUS_995 = [{"target_date": "2026-06-22", "variable": "high",
                  "captured_at": "2026-06-22T19:45:00+00:00",
                  "cli_consensus": 99.5, "sigma_used": 1.0}]
FILL_TS = datetime(2026, 6, 22, 19, 47, tzinfo=timezone.utc)


def test_greater_strike_uses_lower_tail():
    # N(99.5,1): P(T >= 99) ~ 1 - Phi(98.5) = 1 - Phi(-1) = 0.8413
    p, edge, agree = bh.model_at_bet(FILL_TS, "high", 99, None, "greater", "yes",
                                     0.60, CONSENSUS_995, [], calib={})
    assert 0.80 < p < 0.88
    assert agree is True                     # 0.84 > 0.60 entry


def test_less_strike_uses_upper_cdf():
    # N(99.5,1): P(T <= 98) ~ Phi(98.5) = Phi(-1) = 0.1587
    p, edge, agree = bh.model_at_bet(FILL_TS, "high", None, 98, "less", "yes",
                                     0.40, CONSENSUS_995, [], calib={})
    assert 0.12 < p < 0.20
    assert agree is False                    # 0.16 < 0.40 entry


def test_entry_none_gives_prob_but_no_edge():
    p, edge, agree = bh.model_at_bet(FILL_TS, "high", 99, 100, "between", "yes",
                                     None, CONSENSUS_995, [], calib={})
    assert p is not None
    assert edge is None and agree is None
