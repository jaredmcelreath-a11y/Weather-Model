"""Unit tests for the market adapters — verify each adapter wires the model→
contract price mapping to the right model function and carries the right
safe-hold defaults. Uses a synthetic probs dict so the math is independent of
live data."""

from datetime import date

import model
from markets import ROBINHOOD, KALSHI
from sources import kalshi

PROBS = {"<=85": 0.05, "86": 0.05, "87": 0.10, "88": 0.20, "89": 0.25,
         "90": 0.15, "91": 0.10, "92": 0.03, ">=93": 0.07}


def test_implied_forecast_distills_ev_and_pmf(monkeypatch):
    # Three buckets priced so the mass sits on 88-90; EV should land there.
    contracts = [
        {"strike_type": "less", "floor": None, "cap": 86,
         "yes_bid": 0.04, "yes_ask": 0.06, "volume": 10},
        {"strike_type": "between", "floor": 86, "cap": 88,
         "yes_bid": 0.18, "yes_ask": 0.22, "volume": 50},
        {"strike_type": "between", "floor": 88, "cap": 90,
         "yes_bid": 0.55, "yes_ask": 0.65, "volume": 80},
        {"strike_type": "greater", "floor": 90, "cap": None,
         "yes_bid": 0.10, "yes_ask": 0.14, "volume": 20},
    ]
    monkeypatch.setattr(kalshi, "fetch_contracts", lambda v, d: contracts)
    out = kalshi.implied_forecast("high", date(2026, 6, 16))
    assert out is not None
    # PMF normalizes to 1; mass concentrated on the 88-90 bucket (mid 89).
    assert abs(sum(p for *_, p in out["buckets"]) - 1.0) < 1e-6
    assert 88 <= out["ev"] <= 90
    assert out["volume"] == 160.0


def test_market_min_bucket_price_config():
    import config
    assert 0 < config.MARKET_MIN_BUCKET_PRICE < 0.1


def test_implied_forecast_trims_noise_tails(monkeypatch):
    # A locked day: one bucket carries ~90c, flanked by 2c bid/ask-noise tails.
    # Those tails drag the EV off the settled bucket, so trim them before
    # normalizing (both the EV and the reported PMF).
    contracts = [
        {"strike_type": "less", "floor": None, "cap": 90,
         "yes_bid": 0.01, "yes_ask": 0.03, "volume": 5},     # 2c noise, mid 89.5
        {"strike_type": "between", "floor": 90, "cap": 92,
         "yes_bid": 0.88, "yes_ask": 0.92, "volume": 100},   # 90c, mid 91
        {"strike_type": "greater", "floor": 96, "cap": None,
         "yes_bid": 0.01, "yes_ask": 0.03, "volume": 5},     # 2c noise, mid 96.5
    ]
    monkeypatch.setattr(kalshi, "fetch_contracts", lambda v, d: contracts)
    out = kalshi.implied_forecast("high", date(2026, 6, 16))
    assert len(out["buckets"]) == 1                          # only the 90c bucket survives
    assert out["buckets"][0][:2] == [90, 92]
    assert out["ev"] == 91.0                                 # not dragged by the tails
    assert abs(sum(p for *_, p in out["buckets"]) - 1.0) < 1e-6
    assert out["volume"] == 110.0                            # volume still counts all contracts


def test_implied_forecast_keeps_all_when_every_bucket_below_floor(monkeypatch):
    # A flat/illiquid market where every bucket is 1-2c must NOT trim to nothing.
    contracts = [
        {"strike_type": "between", "floor": 88, "cap": 90,
         "yes_bid": 0.01, "yes_ask": 0.03, "volume": 1},
        {"strike_type": "between", "floor": 90, "cap": 92,
         "yes_bid": 0.01, "yes_ask": 0.01, "volume": 1},
    ]
    monkeypatch.setattr(kalshi, "fetch_contracts", lambda v, d: contracts)
    out = kalshi.implied_forecast("high", date(2026, 6, 16))
    assert out is not None and len(out["buckets"]) == 2      # guard kept both


def test_implied_forecast_none_when_unpriced(monkeypatch):
    monkeypatch.setattr(kalshi, "fetch_contracts",
                        lambda v, d: [{"strike_type": "between", "floor": 88,
                                       "cap": 90, "yes_bid": None, "yes_ask": None}])
    assert kalshi.implied_forecast("high", date(2026, 6, 16)) is None


def test_robinhood_uses_prob_for_contract():
    c = {"kind": ">", "strike": 90}
    assert ROBINHOOD.model_prob(PROBS, c) == model.prob_for_contract(PROBS, ">", 90)


def test_kalshi_between_bucket():
    c = {"strike_type": "between", "floor": 88, "cap": 89}
    assert KALSHI.model_prob(PROBS, c) == model.prob_for_strike(PROBS, "between", 88, 89)
    assert round(KALSHI.model_prob(PROBS, c), 4) == 0.45


def test_kalshi_less_bucket():
    c = {"strike_type": "less", "floor": None, "cap": 88}
    assert round(KALSHI.model_prob(PROBS, c), 4) == 0.20


def test_kalshi_greater_bucket():
    c = {"strike_type": "greater", "floor": 91, "cap": None}
    assert round(KALSHI.model_prob(PROBS, c), 4) == 0.10


def test_safe_hold_defaults():
    assert (ROBINHOOD.safe_hold_default, ROBINHOOD.safe_hold_min) == (0.80, 0.60)
    assert (KALSHI.safe_hold_default, KALSHI.safe_hold_min) == (0.55, 0.50)


def test_market_view_imports_and_exposes_render():
    import market_view
    assert callable(market_view.render_page)
    assert callable(market_view.render_variable)


def test_basis_note_kalshi_set_robinhood_none():
    assert ROBINHOOD.basis_note is None
    assert KALSHI.basis_note and "CLI" in KALSHI.basis_note


# --- Kalshi market line on the consensus chart ---

def test_consensus_log_records_market_ev(tmp_path):
    import consensus_log
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from config import TIMEZONE
    tz = ZoneInfo(TIMEZONE)
    now = datetime(2026, 6, 16, 12, tzinfo=tz)
    snap = {
        "updated": now.isoformat(),
        "current": {"temp": 88},
        "today": {"day": "2026-06-16",
                  "high": {"consensus": 95.0},
                  "low": {"consensus": 77.0}},
        # market block as scheduled_log attaches to the CLI snapshot
        "market": {"today": {"high": {"ev": 96.0}}},
    }
    p = str(tmp_path / "hist.jsonl")
    consensus_log.record(snap, path=p, basis="cli")
    by = {(r["target_date"], r["variable"]): r for r in consensus_log.load(p)}
    assert by[("2026-06-16", "high")]["market_ev"] == 96.0
    # low had no market EV -> key omitted (back-compatible)
    assert "market_ev" not in by[("2026-06-16", "low")]


def test_consensus_history_df_includes_kalshi_line():
    import market_view
    rows = [
        {"target_date": "2026-06-16", "variable": "high", "basis": "cli",
         "captured_at": "2026-06-16T10:00:00", "consensus": 95.0, "market_ev": 94.0},
        {"target_date": "2026-06-16", "variable": "high", "basis": "cli",
         "captured_at": "2026-06-16T10:30:00", "consensus": 95.5, "market_ev": 94.5},
    ]
    df = market_view.consensus_history_df(rows, "2026-06-16", "high", "cli",
                                          include_temp=False)
    assert "Kalshi" in df.columns
    assert list(df["Kalshi"]) == [94.0, 94.5]


# --- per-variable time window (declutter the through-the-day chart) ---

def test_consensus_history_df_high_windows_to_daytime():
    # Today's high samples include points captured the previous night/early
    # morning (logged when the day was still "tomorrow"); the window keeps only
    # 8am-10pm of the target day.
    import market_view
    rows = [
        {"target_date": "2026-06-16", "variable": "high", "basis": "cli",
         "captured_at": "2026-06-15T23:00:00", "consensus": 94.0},   # prev night
        {"target_date": "2026-06-16", "variable": "high", "basis": "cli",
         "captured_at": "2026-06-16T06:00:00", "consensus": 80.0},   # pre-8am
        {"target_date": "2026-06-16", "variable": "high", "basis": "cli",
         "captured_at": "2026-06-16T09:00:00", "consensus": 95.0},
        {"target_date": "2026-06-16", "variable": "high", "basis": "cli",
         "captured_at": "2026-06-16T15:00:00", "consensus": 96.0},
    ]
    df = market_view.consensus_history_df(rows, "2026-06-16", "high", "cli",
                                          include_temp=False, is_today=True)
    assert [t.hour for t in df.index] == [9, 15]


def test_consensus_history_df_low_windows_to_overnight():
    # Today's low forms near dawn; keep midnight through 11am, dropping the
    # wasted prior-evening and afternoon-flat stretches.
    import market_view
    rows = [
        {"target_date": "2026-06-16", "variable": "low", "basis": "cli",
         "captured_at": "2026-06-15T23:00:00", "consensus": 79.0},   # before midnight
        {"target_date": "2026-06-16", "variable": "low", "basis": "cli",
         "captured_at": "2026-06-16T00:30:00", "consensus": 78.0},   # just after midnight
        {"target_date": "2026-06-16", "variable": "low", "basis": "cli",
         "captured_at": "2026-06-16T05:00:00", "consensus": 76.0},
        {"target_date": "2026-06-16", "variable": "low", "basis": "cli",
         "captured_at": "2026-06-16T14:00:00", "consensus": 76.0},   # afternoon flat
    ]
    df = market_view.consensus_history_df(rows, "2026-06-16", "low", "cli",
                                          include_temp=False, is_today=True)
    assert [(t.day, t.hour) for t in df.index] == [(16, 0), (16, 5)]


def test_consensus_history_df_future_day_windowed_to_target_day():
    # A future day is windowed to its own active span too (not just today), so a
    # tomorrow low chart drops the daytime lead-up and spans midnight through 11am
    # of the target day — the captures outside that window are clipped.
    import market_view
    rows = [
        {"target_date": "2026-06-17", "variable": "low", "basis": "cli",
         "captured_at": "2026-06-16T21:00:00", "consensus": 90.0},   # prev-evening lead-up
        {"target_date": "2026-06-17", "variable": "low", "basis": "cli",
         "captured_at": "2026-06-17T00:30:00", "consensus": 88.0},   # in window (12:30am)
        {"target_date": "2026-06-17", "variable": "low", "basis": "cli",
         "captured_at": "2026-06-17T05:00:00", "consensus": 85.0},   # in window (5am)
        {"target_date": "2026-06-17", "variable": "low", "basis": "cli",
         "captured_at": "2026-06-17T14:00:00", "consensus": 86.0},   # after 11am
    ]
    df = market_view.consensus_history_df(rows, "2026-06-17", "low", "cli",
                                          include_temp=False, is_today=False)
    assert [(t.day, t.hour) for t in df.index] == [(17, 0), (17, 5)]


def test_accuracy_note_kalshi_set_robinhood_none():
    assert ROBINHOOD.accuracy_note is None
    assert KALSHI.accuracy_note and "CLI" in KALSHI.accuracy_note
