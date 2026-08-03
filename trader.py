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
    # `_GEO` travels with every managed position: the settlement pass scores from
    # it, and the Trader page renders from it. Kalshi's own positions() carries
    # none of it, so the live branch enriches from our recorded entry too.
    _GEO = ("entry_ask", "day", "floor", "cap", "label", "ts")
    entries = runtime.get("entries") or {}
    if mode == "live":
        out = []
        for p in held_truth:
            e = entries.get(p["ticker"], {})
            out.append({**p, **{k: e.get(k) for k in _GEO}})
        return out
    return [{"ticker": tkr, "side": e.get("side"), "count": e.get("count", 1),
             "variable": e.get("variable"), **{k: e.get(k) for k in _GEO}}
            for tkr, e in entries.items()]


def _position_day(pos: dict) -> date | None:
    """The position's climate day: the recorded `day`, else the event-date suffix
    of its ticker (`KXHIGHAUS-26JUL28-B99.5` -> 2026-07-28). The suffix is the
    inverse of `kalshi._event_suffix`, so parsing it back is safe — unlike the
    bracket suffix, whose T-prefixed tails don't follow the B<mid> rule."""
    raw = pos.get("day")
    if raw:
        try:
            return date.fromisoformat(raw)
        except (TypeError, ValueError):
            pass
    parts = (pos.get("ticker") or "").split("-")
    if len(parts) >= 2:
        try:
            return datetime.strptime(parts[1], "%y%b%d").date()
        except ValueError:
            pass
    return None


def bracket_of_ticker(ticker: str | None) -> tuple[float, float] | None:
    """(floor, cap) recovered from a `B<mid>` bracket suffix, else None.

    `KXHIGHTDAL-26JUL28-B100.5` -> (100.0, 101.0): the mid sits half a degree
    inside each edge, so the inverse is exact. The T-prefixed tail contracts are
    open-ended and do NOT follow that rule, so anything else returns None rather
    than a guessed range — a wrong bracket would score a settled trade backwards.
    Only for records that predate `floor`/`cap` being logged; live positions carry
    the real geometry from Kalshi.
    """
    suffix = (ticker or "").split("-")[-1]
    if not suffix.startswith("B"):
        return None
    try:
        mid = float(suffix[1:])
    except ValueError:
        return None
    return mid - 0.5, mid + 0.5


def bracket_won(floor: float, cap: float, side: str | None, value: float) -> bool:
    """Did this position settle in the money? Shared by the loop's settlement pass
    and trade_pnl's scoring of already-settled records, so the two can never
    disagree about what a win is. Kalshi's range brackets are inclusive."""
    yes_won = floor <= value <= cap
    return yes_won if side != "no" else not yes_won


def settle_positions(managed: list[dict], today: date,
                     settled: dict) -> list[dict]:
    """Close decisions for positions whose climate day has passed, scored against
    the CLI settlement rather than the market.

    Kalshi settles these contracts itself, so there is nothing to sell — but the
    loop must still retire them. Left alone, a held-to-settlement position keeps
    occupying its `max_open_per_variable` slot until the reversal path dumps it at
    the `cur_bid or 0.01` fallback, and a settled market's book is empty, so a
    bracket worth $1.00 was booked as a 1c near-total loss.

    `settled` is {day: (high, low)} from `settlements.as_map("cli", ...)`. A day
    with no settlement yet is skipped and retried next run. Pure — no IO — so the
    scoring is unit-testable.
    """
    out = []
    for pos in managed:
        day = _position_day(pos)
        # Unknown day: leave it alone. Retiring on a missing field would drop a
        # still-open LIVE position from management (no stop-loss) the moment its
        # runtime record looked odd.
        if day is None or day >= today:
            continue
        # A pre-schema position has the day (from its ticker) but no bracket, so
        # it can never be scored. Retire it so it stops holding the slot, but
        # never invent a number for the curve.
        if pos.get("floor") is None or pos.get("cap") is None:
            out.append({**pos, "exit_price": None, "pnl": None,
                        "reason": "settled (unscored, pre-schema)"})
            continue
        row = settled.get(day)
        if not row:
            continue                       # not settled yet — retry next run
        value = row[0] if pos.get("variable") == "high" else row[1]
        if value is None:
            continue
        won = bracket_won(pos["floor"], pos["cap"], pos.get("side"), value)
        price = 1.0 if won else 0.0
        entry_ref = pos.get("entry_ask")
        out.append({**pos, "exit_price": price,
                    "pnl": None if entry_ref is None
                    else round((price - entry_ref) * pos["count"], 4),
                    "reason": f"settled {'won' if won else 'lost'} "
                              f"({pos.get('variable')} {value:g})"})
    return out


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

    def persist():
        """Write the runtime the MOMENT it changes, before the audit-log append.

        The runtime is the only record that says "this already happened", so it is
        what makes a pass replay-safe. Persisting it once at the end of run_once
        meant any later exception — and main() swallows them per station — left the
        action done but unrecorded, so the next run repeated it: a second real
        order in live mode, plus duplicate log records that made the P&L and
        win/loss record miscount one trade as two.

        Ordered BEFORE append_log deliberately. Both can fail, and the two
        failures are not equally bad: losing a log line costs an audit record,
        while losing the runtime write costs a duplicate trade. This shrinks the
        exposed window from "the rest of the pass" to a single API call.
        """
        deps.save_runtime(runtime)

    # Per-variable market + model context.
    ctx = {}
    day_snap = (deps.snapshot() or {}).get("today") or {}
    for var in params["enabled_variables"]:
        vs = day_snap.get(var)
        if not vs:
            continue
        contracts = deps.fetch_contracts(var, today)
        implied = deps.implied_forecast(var, today)
        ok_entry, reason = trade_logic.entry_allowed(vs, implied, params, var)
        mkt = trade_logic.market_center(implied)
        # The bracket this pass POINTS AT, computed even when the entry is gated.
        # Observability only. `target` below stays gated exactly as before, because
        # it also drives should_exit's reversal check — an ungated target would make
        # reversals fire on passes that previously held. (entry_allowed already
        # returns False when mkt is None, so target is unchanged by construction.)
        # Without this the log carries no target at all on a gated pass, which made
        # every entry-gate counterfactual unmeasurable.
        intent = (trade_logic.select_bracket(contracts, mkt, var)
                  if mkt is not None else None)
        target = intent if ok_entry else None
        gates_ok, _ = trade_logic.gates_clear(vs)
        ctx[var] = {"vs": vs, "contracts": contracts, "implied": implied,
                    "target": target, "intent": intent,
                    "ok_entry": ok_entry, "reason": reason,
                    "gates_ok": gates_ok,
                    "by_ticker": {c["ticker"]: c for c in contracts}}

    managed = _managed_positions(mode, held_truth, runtime)

    # ---- SETTLEMENT pass (before exits) ----
    # Must run first: once a position's day has passed, the exit pass would see a
    # moved target, call it a reversal, and sell into an empty book at 0.01.
    exited: set[str] = set()
    import settlements
    try:
        settled_map = settlements.as_map("cli", station=station)
    except Exception as e:
        # Never fatal — a bad read just defers every settlement to the next run.
        # But say so: the silent version of this cost two days of stuck positions.
        print(f"[{station}] settlement map unavailable: {e}")
        settled_map = {}
    for closed in settle_positions(managed, today, settled_map):
        runtime["entries"].pop(closed["ticker"], None)
        exited.add(closed["ticker"])
        persist()
        deps.append_log(trade_log.build_record(
            "exit", ticker=closed["ticker"], side=closed.get("side"),
            count=closed.get("count"), variable=closed.get("variable"),
            reason=closed["reason"], exit_price=closed["exit_price"],
            pnl=closed["pnl"], mode=mode))
        summary["exits"] += 1

    # A past-day position still held after that pass is an anomaly, not routine:
    # the CLI settlement is recorded the same evening, so by the time the next
    # market window opens it is on file. The only reason to still be holding one
    # is that the loop cannot see the settlement — which is exactly the condition
    # that went unnoticed for days, because it looks identical to "no positions
    # to settle". Print it so the Action log names the stuck ticker.
    overdue = [p["ticker"] for p in managed
               if p["ticker"] not in exited
               and (_position_day(p) or today) < today]
    if overdue:
        print(f"[{station}] settlement pending for {len(overdue)} past-day "
              f"position(s): {', '.join(overdue)} "
              f"({len(settled_map)} settled day(s) on file)")

    # ---- EXIT pass ----
    mark_value = 0.0
    for pos in managed:
        if pos["ticker"] in exited:
            continue
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
            # The prices the stop-loss was evaluated against on a pass that did
            # NOT exit. Entry/exit records alone leave the held interval blind, so
            # a stop that never fires is indistinguishable from one that was never
            # correctly evaluated — the exact question the 2026-08-02 KAUS review
            # could not answer.
            deps.append_log(trade_log.build_record(
                "hold", ticker=pos["ticker"], variable=var, side=pos["side"],
                count=pos["count"], entry_ask=pos["entry_ask"],
                current_ask=cur_ask, current_bid=cur_bid,
                stop_at=round(pos["entry_ask"] - params["stop_loss"], 4),
                target_ticker=target_tkr, mode=mode))
            continue
        price = cur_bid if cur_bid is not None else 0.01
        deps.place_order(ticker=pos["ticker"], side=pos["side"], action="sell",
                         count=pos["count"], price=price,
                         client_order_id=f"{pos['ticker']}:{today_iso}:exit:{bucket}",
                         mode=mode)
        runtime["entries"].pop(pos["ticker"], None)
        exited.add(pos["ticker"])
        persist()
        if "stop" in why:
            just_stopped[var] = pos["ticker"]
        # exit_price/pnl make the round trip scorable — without them trade_pnl
        # cannot tell a 20c loss from a 20c gain.
        entry_ref = pos.get("entry_ask")
        deps.append_log(trade_log.build_record(
            "exit", ticker=pos["ticker"], side=pos["side"], count=pos["count"],
            reason=why, mode=mode, variable=var, exit_price=price,
            pnl=None if entry_ref is None
            else round((price - entry_ref) * pos["count"], 4)))
        summary["exits"] += 1

    # ---- Daily-loss circuit breaker (LIVE only — shadow has no real fills to mark) ----
    # NOTE: enforced only in live mode; not exercised by the unit tests, so it must
    # be watched during the shadow->live cutover before size is scaled.
    if mode == "live" and params.get("daily_loss_cap") is not None:
        equity = bal + mark_value
        day_eq = runtime.setdefault("day_start_equity", {})
        if today_iso not in day_eq:
            # Persist the anchor at once: it is the reference the cap measures
            # against, and re-taking it next run after a crash would anchor to a
            # lower equity, quietly raising the loss the cap tolerates.
            day_eq[today_iso] = equity
            persist()
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
                deps.append_log(trade_log.build_record(
                    "skip", variable=var, reason=c["reason"], mode=mode,
                    intent_ticker=(c["intent"] or {}).get("ticker")))
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
        # The bracket geometry and climate day travel with the position, in BOTH
        # the runtime record and the audit log: the settlement pass scores a
        # position from its runtime record, and trade_pnl scores it from the log.
        # Recovering either by parsing the ticker is brittle — the T-prefixed tail
        # contracts don't follow the B<mid> bracket rule.
        geo = {"variable": var, "day": today_iso, "floor": target.get("floor"),
               "cap": target.get("cap"), "label": target.get("label")}
        runtime["entries"][target["ticker"]] = {
            "entry_ask": entry_ask, "side": sizing["side"],
            "count": sizing["contracts"], "ts": now.isoformat(), **geo}
        persist()
        open_by_var[var] = open_by_var.get(var, 0) + 1
        deps.append_log(trade_log.build_record("entry", ticker=target["ticker"],
                        side=sizing["side"], count=sizing["contracts"],
                        entry_ask=entry_ask, stake=sizing["stake"], mode=mode,
                        **geo))
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
