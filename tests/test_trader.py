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
