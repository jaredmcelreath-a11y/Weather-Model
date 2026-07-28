"""Autonomous trading orchestrator — one pass per run.

Reconciles truth from the Kalshi account, consults the model/kelly decision
engine, then runs an EXIT pass followed by an ENTRY pass, placing marketable-limit
orders through the isolated write client. Every external call is reached through a
`Deps` bundle so the whole loop is unit-testable with fakes and no network. Ships
SAFE: does nothing unless kill_switch is off AND (for real fills) mode is live.

Position tracking: in LIVE mode the managed set reconciles from Kalshi positions()
(the source of truth), enriched with the entry_ask we recorded at buy time. In
SHADOW mode nothing actually fills, so the managed set is our own runtime["entries"]
record — otherwise a shadow run would re-buy every tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

import config
import trade_logic
import trade_log
import trade_params


@dataclass
class Deps:
    load_state: Callable
    load_runtime: Callable
    save_runtime: Callable
    snapshot: Callable
    balance: Callable
    positions: Callable
    fetch_contracts: Callable
    fetch_orderbook: Callable
    implied_forecast: Callable
    place_order: Callable
    append_log: Callable
    notify: Callable


def _best_ask(book: dict, side: str):
    """Best current ask for the held/target side, from the normalized book."""
    from sources import kalshi as _k
    ladder = _k.ask_ladder(book, side)
    return ladder[0][0] if ladder else None


def _best_bid(book: dict, side: str):
    """Best resting bid on `side` — the price a marketable SELL fills into, and
    the mark used for equity."""
    bids = book.get(side) or []
    return max((p for p, _s in bids), default=None)


def _managed_positions(mode: str, held_truth: list, runtime: dict) -> list[dict]:
    """The positions to manage this run. Live → Kalshi truth enriched with the
    recorded entry_ask; shadow → our own runtime record (no real fills exist)."""
    entries = runtime.get("entries") or {}
    if mode == "live":
        out = []
        for p in held_truth:
            e = entries.get(p["ticker"], {})
            out.append({**p, "entry_ask": e.get("entry_ask")})
        return out
    return [{"ticker": tkr, "side": e.get("side"), "count": e.get("count", 1),
             "variable": e.get("variable"), "entry_ask": e.get("entry_ask")}
            for tkr, e in entries.items()]


def run_once(now: datetime | None = None, *, deps: Deps,
             station: str = config.DEFAULT_STATION) -> dict:
    import settlement
    now = now or datetime.now()
    params = deps.load_state()

    if params["kill_switch"]:
        return {"halted": "kill_switch"}
    if not trade_params.within_market_window(now, params):
        return {"halted": "closed"}

    today: date = settlement.climate_day_of(now, station)
    today_iso = today.isoformat()
    runtime = deps.load_runtime() or {}
    runtime.setdefault("entries", {})
    if runtime.get("halt_day") == today_iso:
        return {"halted": "daily_loss"}

    bal = deps.balance()
    if bal is None:
        return {"halted": "reconcile_failed"}
    held_truth = deps.positions()

    mode = params["mode"]
    bucket = now.strftime("%Y%m%dT%H%M")          # idempotency run-bucket
    summary = {"entries": 0, "exits": 0}
    just_stopped: dict[str, str] = {}             # variable -> ticker stopped this run

    # Per-variable market + model context.
    ctx = {}
    day_snap = (deps.snapshot() or {}).get("today") or {}
    for var in params["enabled_variables"]:
        vs = day_snap.get(var)
        if not vs:
            continue
        contracts = deps.fetch_contracts(var, today)
        implied = deps.implied_forecast(var, today)
        target = None
        ok_entry, reason = trade_logic.entry_allowed(vs, implied, params, var)
        if ok_entry:
            mkt = trade_logic.market_center(implied)
            target = trade_logic.select_bracket(contracts, mkt, var)
        gates_ok, _ = trade_logic.gates_clear(vs)
        ctx[var] = {"vs": vs, "contracts": contracts, "implied": implied,
                    "target": target, "ok_entry": ok_entry, "reason": reason,
                    "gates_ok": gates_ok,
                    "by_ticker": {c["ticker"]: c for c in contracts}}

    managed = _managed_positions(mode, held_truth, runtime)

    # ---- EXIT pass (before entry) ----
    exited: set[str] = set()
    mark_value = 0.0
    for pos in managed:
        var = pos["variable"]
        c = ctx.get(var)
        book = deps.fetch_orderbook(pos["ticker"])
        cur_bid = _best_bid(book, pos["side"]) if pos["side"] else None
        cur_ask = _best_ask(book, pos["side"]) if pos["side"] else None
        if cur_bid is not None:
            mark_value += pos["count"] * cur_bid
        if c is None or pos.get("entry_ask") is None:
            continue                              # can't stop-loss without an entry ref
        target_tkr = c["target"]["ticker"] if c["target"] else None
        do_exit, why = trade_logic.should_exit(pos, cur_ask, target_tkr,
                                               c["gates_ok"], params)
        if not do_exit:
            continue
        deps.place_order(ticker=pos["ticker"], side=pos["side"], action="sell",
                         count=pos["count"],
                         price=cur_bid if cur_bid is not None else 0.01,
                         client_order_id=f"{pos['ticker']}:{today_iso}:exit:{bucket}",
                         mode=mode)
        runtime["entries"].pop(pos["ticker"], None)
        exited.add(pos["ticker"])
        if "stop" in why:
            just_stopped[var] = pos["ticker"]
        deps.append_log(trade_log.build_record("exit", ticker=pos["ticker"],
                        side=pos["side"], count=pos["count"], reason=why, mode=mode))
        summary["exits"] += 1

    # ---- Daily-loss circuit breaker (LIVE only — shadow has no real fills to mark) ----
    # NOTE: enforced only in live mode; not exercised by the unit tests, so it must
    # be watched during the shadow->live cutover before size is scaled.
    if mode == "live" and params.get("daily_loss_cap") is not None:
        equity = bal + mark_value
        day_eq = runtime.setdefault("day_start_equity", {})
        if today_iso not in day_eq:
            day_eq[today_iso] = equity
        elif equity - day_eq[today_iso] <= params["daily_loss_cap"]:
            runtime["halt_day"] = today_iso
            deps.save_runtime(runtime)
            deps.notify("Trader halted", f"Daily loss cap hit (${params['daily_loss_cap']:.2f}).")
            deps.append_log(trade_log.build_record("halt", reason="daily_loss",
                            equity=round(equity, 2), mode=mode))
            return {"halted": "daily_loss", **summary}

    # Open count per variable AFTER exits.
    open_by_var: dict[str, int] = {}
    for pos in managed:
        if pos["ticker"] in exited:
            continue
        open_by_var[pos["variable"]] = open_by_var.get(pos["variable"], 0) + 1

    # ---- ENTRY pass ----
    for var in params["enabled_variables"]:
        c = ctx.get(var)
        if not c or not c["ok_entry"] or not c["target"]:
            if c and not c["ok_entry"]:
                deps.append_log(trade_log.build_record("skip", variable=var,
                                reason=c["reason"], mode=mode))
            continue
        if open_by_var.get(var, 0) >= params["max_open_per_variable"]:
            deps.append_log(trade_log.build_record("skip", variable=var,
                            reason="max_open_per_variable", mode=mode))
            continue
        target = c["target"]
        if not trade_logic.reentry_allowed(just_stopped.get(var), target["ticker"]):
            deps.append_log(trade_log.build_record("skip", variable=var,
                            reason="re-entry into just-stopped bracket", mode=mode))
            continue
        book = deps.fetch_orderbook(target["ticker"])
        sizing = trade_logic.size_bracket(target, c["vs"], book, bal, params)
        if sizing["contracts"] <= 0:
            deps.append_log(trade_log.build_record("skip", variable=var,
                            ticker=target["ticker"], reason=sizing["note"], mode=mode))
            continue
        entry_ask = sizing["avg_price"]
        deps.place_order(ticker=target["ticker"], side=sizing["side"], action="buy",
                         count=sizing["contracts"], price=entry_ask,
                         client_order_id=f"{target['ticker']}:{today_iso}:entry:{bucket}",
                         mode=mode)
        runtime["entries"][target["ticker"]] = {
            "entry_ask": entry_ask, "side": sizing["side"],
            "count": sizing["contracts"], "variable": var, "ts": now.isoformat()}
        open_by_var[var] = open_by_var.get(var, 0) + 1
        deps.append_log(trade_log.build_record("entry", ticker=target["ticker"],
                        side=sizing["side"], count=sizing["contracts"],
                        entry_ask=entry_ask, stake=sizing["stake"], mode=mode))
        summary["entries"] += 1

    deps.save_runtime(runtime)
    return {"halted": None, **summary}


def _real_deps(station: str = config.DEFAULT_STATION) -> Deps:
    import calibration
    import model
    import notify
    import trade_state
    from sources import kalshi, kalshi_orders, kalshi_portfolio

    def snapshot():
        # Model on the basis Kalshi SETTLES on — the continuous NWS CLI daily
        # max/min — exactly as app.py's Kalshi page does. The plain hourly-basis
        # snapshot runs ~1-2°F cooler on highs, so comparing it to the CLI-basis
        # market ev made `agreement_tol` reject genuine agreement (2026-07-28
        # KAUS: hourly 96.4 vs market 98.97 -> skip, while the CLI basis was 98.2,
        # inside tolerance) and shifted every bin fed to prob_for_strike.
        calib = calibration.get(refresh=True, station=station)
        if not calib:
            return {}
        return model.snapshot(calib, settle_offset=calib.get("settlement_offset"),
                              continuous_obs=True, station=station)

    def positions():
        # Isolation invariant: manage only THIS city's markets, never the other's.
        return [p for p in (kalshi_portfolio.positions() or [])
                if kalshi.station_of_ticker(p["ticker"]) == station]

    return Deps(
        load_state=lambda: trade_state.load_state(station=station),
        load_runtime=lambda: trade_state.load_runtime(station=station),
        save_runtime=lambda r: trade_state.save_runtime(r, station=station),
        snapshot=snapshot,
        balance=kalshi_portfolio.balance,
        positions=positions,
        fetch_contracts=lambda v, d: kalshi.fetch_contracts(v, d, station=station),
        fetch_orderbook=kalshi.fetch_orderbook,
        implied_forecast=lambda v, d: kalshi.implied_forecast(v, d, station=station),
        place_order=kalshi_orders.place_order,
        append_log=lambda rec: trade_state.append_jsonl(
            trade_state._path(trade_state.LOG_PATH, station), rec),
        notify=notify.send_ntfy,
    )


def main() -> None:
    """Run one pass for every configured station, isolating failures so one
    city's outage never blocks the other. Each city is gated independently by
    its own kill switch / mode / daily-loss halt."""
    from sources.common import TZ
    now = datetime.now(TZ)
    for code in config.STATION_CODES:
        try:
            out = run_once(now=now, deps=_real_deps(code), station=code)
            print(f"[{code}] trader run: {out}")
        except Exception as e:
            print(f"[{code}] trader run failed: {e}")


if __name__ == "__main__":
    main()
