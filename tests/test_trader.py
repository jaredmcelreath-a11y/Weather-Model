from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import trader


@dataclass
class Deps:
    state: dict
    snap: dict
    contracts: dict          # variable -> list[contract]
    book: dict               # ticker -> normalized orderbook
    implied: dict            # variable -> implied dict
    bal: float = 10.0
    positions_list: list = field(default_factory=list)
    runtime: dict = field(default_factory=dict)
    orders: list = field(default_factory=list)
    logs: list = field(default_factory=list)

    # injected callables
    def load_state(self): return self.state
    def load_runtime(self): return self.runtime
    def save_runtime(self, r): self.runtime = r
    def snapshot(self): return self.snap
    def balance(self): return self.bal
    def positions(self): return self.positions_list
    def fetch_contracts(self, var, day): return self.contracts.get(var, [])
    def fetch_orderbook(self, ticker): return self.book.get(ticker, {"yes": [], "no": []})
    def implied_forecast(self, var, day): return self.implied.get(var)
    def place_order(self, **kw): self.orders.append(kw); return {"shadow": True, **kw}
    def append_log(self, rec): self.logs.append(rec)
    def notify(self, title, msg): pass


def _live_params(**over):
    base = {"kill_switch": False, "mode": "shadow", "enabled_variables": ["high"],
            "min_resolved": 0.70, "agreement_tol": 1.0, "max_price": 0.94,
            "min_price": 0.10, "kelly_fraction": 0.25, "per_market_cap": 0.50,
            "max_open_per_variable": 1, "daily_loss_cap": None, "stop_loss": 0.20,
            "slippage_cap": 0.02, "market_open": "06:00", "market_close": "20:00"}
    base.update(over)
    return base


NOON = datetime(2026, 7, 24, 12, 0, tzinfo=ZoneInfo("America/Chicago"))

# A cheap-enough book so one contract fits the $0.50 per-market cap:
#   yes ask = 1 - (best no bid 0.55) = 0.45; one contract ~= 0.47 with fee.
_CHEAP_BOOK = {"yes": [[0.44, 50]], "no": [[0.55, 50]]}


def _high_snap(consensus=98.4):
    return {"today": {"day": "2026-07-24",
                      "high": {"consensus": consensus, "resolved": 0.9,
                               "low_forming": False, "peak_locked": True,
                               "front_widened": False, "convective_widened": False,
                               "probabilities": {"97": 0.2, "98": 0.4, "99": 0.4}}}}


def _b99():
    return {"ticker": "KXHIGHTDAL-26JUL24-B99", "strike_type": "between",
            "floor": 98, "cap": 99, "yes_ask": 0.45, "yes_bid": 0.44,
            "no_ask": 0.55, "no_bid": 0.55}


def test_kill_switch_blocks_everything():
    d = Deps(state=_live_params(kill_switch=True), snap={}, contracts={}, book={},
             implied={})
    out = trader.run_once(now=NOON, deps=d)
    assert out["halted"] == "kill_switch"
    assert d.orders == []


def test_outside_window_no_entry():
    d = Deps(state=_live_params(), snap={}, contracts={}, book={}, implied={})
    early = datetime(2026, 7, 24, 4, 0, tzinfo=ZoneInfo("America/Chicago"))
    out = trader.run_once(now=early, deps=d)
    assert out["halted"] == "closed"


def test_reconcile_failure_trades_nothing():
    d = Deps(state=_live_params(), snap={}, contracts={}, book={}, implied={}, bal=None)
    out = trader.run_once(now=NOON, deps=d)
    assert out["halted"] == "reconcile_failed"
    assert d.orders == []


def test_entry_when_agreement_and_gates_clear():
    tkr = "KXHIGHTDAL-26JUL24-B99"
    d = Deps(state=_live_params(), snap=_high_snap(), contracts={"high": [_b99()]},
             book={tkr: _CHEAP_BOOK}, implied={"high": {"ev": 98.6}})
    out = trader.run_once(now=NOON, deps=d)
    buys = [o for o in d.orders if o["action"] == "buy"]
    assert len(buys) == 1
    assert buys[0]["ticker"] == tkr
    # runtime records the entry so later runs know we hold it.
    assert tkr in d.runtime["entries"]


def test_no_entry_when_disagree():
    tkr = "KXHIGHTDAL-26JUL24-B99"
    d = Deps(state=_live_params(), snap=_high_snap(), contracts={"high": [_b99()]},
             book={tkr: _CHEAP_BOOK}, implied={"high": {"ev": 101.0}})  # >1°F off
    out = trader.run_once(now=NOON, deps=d)
    assert d.orders == []


def test_respects_one_open_per_variable():
    tkr = "KXHIGHTDAL-26JUL24-B99"
    d = Deps(
        state=_live_params(), snap=_high_snap(), contracts={"high": [_b99()]},
        book={tkr: _CHEAP_BOOK}, implied={"high": {"ev": 98.6}},
        runtime={"entries": {tkr: {"entry_ask": 0.45, "side": "yes", "count": 1,
                                   "variable": "high"}}},
    )
    out = trader.run_once(now=NOON, deps=d)
    # already hold the target bracket at max_open=1 -> no new buy, no exit.
    assert all(o["action"] != "buy" for o in d.orders)
    assert all(o["action"] != "sell" for o in d.orders)


def test_reversal_sells_stale_bracket():
    stale = "KXHIGHTDAL-26JUL24-B97"        # held, but target is now B99
    d = Deps(
        state=_live_params(), snap=_high_snap(), contracts={"high": [_b99()]},
        book={"KXHIGHTDAL-26JUL24-B99": _CHEAP_BOOK}, implied={"high": {"ev": 98.6}},
        runtime={"entries": {stale: {"entry_ask": 0.55, "side": "yes", "count": 1,
                                     "variable": "high"}}},
    )
    out = trader.run_once(now=NOON, deps=d)
    sells = [o for o in d.orders if o["action"] == "sell"]
    assert len(sells) == 1 and sells[0]["ticker"] == stale


def test_real_deps_snapshot_uses_the_kalshi_settlement_basis(monkeypatch):
    """The trader must model on the SAME basis Kalshi settles on.

    Kalshi resolves on the continuous NWS CLI daily max/min; the plain snapshot is
    the hourly basis, which runs ~1-2°F cooler on highs. Comparing an hourly-basis
    consensus against the CLI-basis market EV made the agreement gate reject real
    agreement: on 2026-07-28 KAUS read 96.4 (hourly) vs a 98.97 market and skipped,
    while the CLI basis the Forecast page shows was 98.2 — inside agreement_tol. It
    also mis-prices every bin fed to prob_for_strike. Mirrors app.py's own call.
    """
    import calibration
    import model

    calib = {"settlement_offset": {"high": 0.9, "low": -0.4}}
    monkeypatch.setattr(calibration, "get", lambda **kw: calib)
    seen = {}

    def fake_snapshot(c, **kw):
        seen.update(kw)
        return {"today": {}}

    monkeypatch.setattr(model, "snapshot", fake_snapshot)
    trader._real_deps("KAUS").snapshot()
    assert seen.get("settle_offset") == calib["settlement_offset"]
    assert seen.get("continuous_obs") is True
    assert seen.get("station") == "KAUS"


# --- Task 1: records must carry what P&L scoring needs ----------------------

def _b99_labelled():
    c = _b99()
    c["label"] = "98° to 99°"
    return c


def test_entry_record_and_runtime_carry_bracket_and_day():
    """Scoring a position against settlement needs its bracket and climate day.
    Parsing them back out of the ticker is brittle — the T-prefixed tails don't
    follow the bracket rule — so both the log and the runtime record carry them."""
    d = Deps(state=_live_params(), snap=_high_snap(),
             contracts={"high": [_b99_labelled()]},
             book={"KXHIGHTDAL-26JUL24-B99": _CHEAP_BOOK},
             implied={"high": {"ev": 98.6}})
    trader.run_once(now=NOON, deps=d)
    entry = [r for r in d.logs if r["kind"] == "entry"][0]
    for key, val in (("variable", "high"), ("day", "2026-07-24"),
                     ("floor", 98), ("cap", 99), ("label", "98° to 99°")):
        assert entry[key] == val, f"entry record missing {key}"
    rt = d.runtime["entries"]["KXHIGHTDAL-26JUL24-B99"]
    for key, val in (("variable", "high"), ("day", "2026-07-24"),
                     ("floor", 98), ("cap", 99), ("label", "98° to 99°")):
        assert rt[key] == val, f"runtime entry missing {key}"


def test_exit_record_carries_price_and_pnl():
    # Held at 0.60, ask has collapsed to 0.20 -> stop-loss; sells into the 0.18 bid.
    tkr = "KXHIGHTDAL-26JUL24-B99"
    book = {"yes": [[0.18, 50]], "no": [[0.80, 50]]}   # yes bid 0.18, yes ask 0.20
    d = Deps(state=_live_params(), snap=_high_snap(), contracts={"high": [_b99()]},
             book={tkr: book}, implied={"high": {"ev": 98.6}},
             runtime={"entries": {tkr: {"entry_ask": 0.60, "side": "yes", "count": 2,
                                        "variable": "high", "day": "2026-07-24"}}})
    trader.run_once(now=NOON, deps=d)
    ex = [r for r in d.logs if r["kind"] == "exit"][0]
    assert ex["exit_price"] == 0.18
    assert ex["pnl"] == round((0.18 - 0.60) * 2, 4)


# --- Task 2: settle past-day positions from the CLI settlement --------------
# Without this a position held to settlement lingers (occupying the
# max_open_per_variable slot) until the reversal path dumps it at the
# `cur_bid or 0.01` fallback — and a settled market's book is empty, so a
# bracket that settled YES at $1.00 was booked as a 1c near-total loss.

from datetime import date as _date

_YEST = _date(2026, 7, 23)
_TODAY = _date(2026, 7, 24)


def _pos(**over):
    p = {"ticker": "KXHIGHTDAL-26JUL23-B99", "side": "yes", "count": 1,
         "variable": "high", "entry_ask": 0.60, "day": "2026-07-23",
         "floor": 98, "cap": 99}
    p.update(over)
    return p


def test_settle_closes_a_winning_past_day_position_at_one():
    out = trader.settle_positions([_pos()], _TODAY, {_YEST: (99.0, 80.0)})
    assert len(out) == 1
    assert out[0]["exit_price"] == 1.0
    assert out[0]["pnl"] == round((1.0 - 0.60) * 1, 4)
    assert "won" in out[0]["reason"]


def test_settle_closes_a_losing_past_day_position_at_zero():
    out = trader.settle_positions([_pos()], _TODAY, {_YEST: (101.0, 80.0)})
    assert out[0]["exit_price"] == 0.0
    assert out[0]["pnl"] == round(-0.60, 4)
    assert "lost" in out[0]["reason"]


def test_settle_scores_the_low_against_the_low_settlement():
    low = _pos(ticker="KXLOWTDAL-26JUL23-B79", variable="low", floor=79, cap=80)
    assert trader.settle_positions([low], _TODAY, {_YEST: (99.0, 80.0)})[0]["exit_price"] == 1.0


def test_settle_inverts_for_a_no_position():
    no_pos = _pos(side="no")
    # High settled 99, inside 98-99 -> YES wins, so NO loses.
    assert trader.settle_positions([no_pos], _TODAY, {_YEST: (99.0, 80.0)})[0]["exit_price"] == 0.0


def test_settle_defers_when_the_day_is_not_settled_yet():
    assert trader.settle_positions([_pos()], _TODAY, {}) == []


def test_settle_leaves_todays_position_alone():
    assert trader.settle_positions([_pos(day="2026-07-24")], _TODAY,
                                   {_TODAY: (99.0, 80.0)}) == []


def test_settle_closes_a_pre_schema_position_unscored():
    # No day/floor/cap (written before the schema change). The day still comes
    # back off the ticker suffix, so the slot is freed — but with no bracket it
    # can never be scored, and we never invent a number for the curve.
    stale = {"ticker": "KXHIGHAUS-26JUL28-B99.5", "side": "yes", "count": 1,
             "variable": "high", "entry_ask": 0.70}
    out = trader.settle_positions([stale], _date(2026, 7, 29),
                                  {_date(2026, 7, 28): (99.0, 80.0)})
    assert len(out) == 1
    assert out[0]["pnl"] is None
    assert "pre-schema" in out[0]["reason"]


def test_settle_leaves_an_unparseable_position_alone():
    # Retiring on a missing/odd day would drop a still-open LIVE position from
    # management (losing its stop-loss). Only a KNOWN past day retires.
    mystery = {"ticker": "WEIRD", "side": "yes", "count": 1, "variable": "high",
               "entry_ask": 0.60}
    assert trader.settle_positions([mystery], _TODAY, {_YEST: (99.0, 80.0)}) == []


def test_run_once_settles_and_frees_the_position_slot(monkeypatch):
    import settlements
    monkeypatch.setattr(settlements, "as_map",
                        lambda *a, **kw: {_YEST: (99.0, 80.0)})
    stale = "KXHIGHTDAL-26JUL23-B99"
    d = Deps(state=_live_params(), snap=_high_snap(), contracts={"high": [_b99()]},
             book={"KXHIGHTDAL-26JUL24-B99": _CHEAP_BOOK},
             implied={"high": {"ev": 98.6}},
             runtime={"entries": {stale: {"entry_ask": 0.60, "side": "yes",
                                          "count": 1, "variable": "high",
                                          "day": "2026-07-23", "floor": 98,
                                          "cap": 99}}})
    trader.run_once(now=NOON, deps=d)
    settled = [r for r in d.logs if r["kind"] == "exit" and "settled" in r["reason"]]
    assert len(settled) == 1 and settled[0]["exit_price"] == 1.0
    assert stale not in d.runtime["entries"]
    # No order is placed — Kalshi settles the contract itself.
    assert all(o["ticker"] != stale for o in d.orders)
    # The freed slot lets today's entry through in the same run.
    assert any(r["kind"] == "entry" for r in d.logs)
