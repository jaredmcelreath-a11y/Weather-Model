"""Turn the trader's audit log into realized trades, daily P&L, and an equity
curve that starts at zero.

Pure — every function takes plain records and returns plain data, so the shadow
run can be scored without any network or Streamlit. IO lives in trade_state
(reading the log) and trade_view (rendering).

Deliberately separate from `bet_history`'s equity curve: that one is anchored to
a real STARTING_BANKROLL and bound to the Kalshi fill schema. This one measures a
strategy from 0, over the trader's own entry/exit records.

Records predating the 2026-07-28 schema change carry no `exit_price`. A position
that was HELD TO SETTLEMENT is still recoverable — its bracket is embedded in the
ticker and the CLI settlement is on file — so `closed_trades` scores those from
the settlement map. One sold on the market (a reversal or stop-loss) recorded no
sale price anywhere, so it stays SKIPPED, never counted as zero: a missing price
is not a break-even trade.
"""
from __future__ import annotations

from datetime import date, timedelta

import trader


def _day_of(rec: dict) -> date | None:
    """The weather day a record belongs to — its recorded `day`, else the event
    date in its ticker. Reuses the trader's own resolution so the page and the
    loop can never disagree about which day a position belongs to."""
    return trader._position_day(rec)


def _settled_price(entry: dict, rec: dict, settled: dict | None) -> float | None:
    """1.00/0.00 for a position the loop retired at settlement without a price, or
    None when it can't be scored honestly.

    Only for `settled` exits: those were never sold, so the settlement IS the exit
    price. A market exit (reversal, stop-loss) that logged no price sold at some
    unrecorded bid and must stay unscored. The bracket comes from the record when
    present, else from the ticker (`trader.bracket_of_ticker`, which refuses the
    open-ended tails).
    """
    if not settled or "settled" not in (rec.get("reason") or ""):
        return None
    day = _day_of(entry) or _day_of(rec)
    row = settled.get(day) if day else None
    if not row:
        return None
    tkr = entry.get("ticker") or rec.get("ticker")
    var = entry.get("variable") or rec.get("variable")
    if var not in ("high", "low"):
        from sources import kalshi
        var = kalshi.variable_of_ticker(tkr)
    if var not in ("high", "low"):
        return None
    value = row[0] if var == "high" else row[1]
    if value is None:
        return None
    floor, cap = entry.get("floor"), entry.get("cap")
    if floor is None or cap is None:
        found = trader.bracket_of_ticker(tkr)
        if not found:
            return None
        floor, cap = found
    side = entry.get("side") or rec.get("side")
    return 1.0 if trader.bracket_won(floor, cap, side, value) else 0.0


def _drop_lost_exits(records: list[dict]) -> list[dict]:
    """Drop exit records the loop logged but never actually performed.

    `run_once` appends to the audit log before persisting its runtime, so a crash
    in between leaves an exit on record that did not take effect — the position was
    still held on the next run. A later `settled` exit for the SAME ticker is proof
    of exactly that: the settlement pass only retires positions still in
    `runtime["entries"]`, so that position outlived the earlier record.

    Only UNPRICED stale exits are dropped. A recorded sale price is evidence the
    sale really happened, so a genuine stop-loss, re-entry, and later settlement
    stays two honest round trips.

    Without this, Austin's 2026-07-28 win was invisible: a phantom reversal exit
    consumed the entry, leaving the real settlement with nothing to pair against.
    """
    settled_at: dict[str, str] = {}
    for r in records:
        if r.get("kind") == "exit" and "settled" in (r.get("reason") or ""):
            tkr = r.get("ticker")
            settled_at[tkr] = max(settled_at.get(tkr, ""), r.get("ts") or "")
    if not settled_at:
        return records
    return [r for r in records
            if not (r.get("kind") == "exit"
                    and r.get("exit_price") is None
                    and "settled" not in (r.get("reason") or "")
                    and (r.get("ts") or "") < settled_at.get(r.get("ticker"), ""))]


def closed_trades(records: list[dict], settled: dict | None = None) -> list[dict]:
    """Completed round trips, oldest first.

    Pairs each `entry` with the next `exit` on the same ticker, so a bracket that
    is entered, stopped out, and re-entered yields two trades rather than one.
    Entries still open are skipped, as are exits that carry no scorable price.

    `settled` is {day: (high, low)} on the CLI basis; passing it recovers positions
    the loop retired at settlement before `exit_price` was logged.

    REPLAYED RECORDS: `run_once` appends to the audit log immediately but persists
    its runtime only at the end of the pass, so an exception in between makes the
    next run repeat the action — the log then holds two entries (or several exits)
    for one real position. Only ONE position per ticker can be open at a time
    (`runtime["entries"]` is keyed by ticker), so a fresh entry on a ticker already
    open REPLACES it, keeping the latest ask exactly as the runtime overwrite did.
    A duplicate exit finds no open entry and is dropped, and `_drop_lost_exits`
    removes exits that were logged but never took effect. Without all three the
    curve and the win/loss record miscount a single trade.
    """
    open_by_ticker: dict[str, dict] = {}
    out = []
    for rec in sorted(_drop_lost_exits(records), key=lambda r: r.get("ts") or ""):
        kind, tkr = rec.get("kind"), rec.get("ticker")
        if not tkr:
            continue
        if kind == "entry":
            open_by_ticker[tkr] = rec         # replay-safe: one open per ticker
        elif kind == "exit":
            entry = open_by_ticker.pop(tkr, None)
            if entry is None:
                continue                      # exit with no matching entry
            price = rec.get("exit_price")
            if price is None:
                price = _settled_price(entry, rec, settled)
            if price is None:
                continue                      # unscorable, not zero
            ask, count = entry.get("entry_ask"), entry.get("count") or 1
            pnl = rec.get("pnl")
            if pnl is None and ask is not None:
                pnl = round((price - ask) * count, 4)
            if pnl is None:
                continue
            out.append({
                "ticker": tkr,
                "variable": entry.get("variable") or rec.get("variable"),
                "day": _day_of(entry) or _day_of(rec),
                "entry_ts": entry.get("ts"), "exit_ts": rec.get("ts"),
                "entry_ask": ask, "exit_price": price, "count": count,
                "pnl": pnl, "reason": rec.get("reason", ""),
            })
    return [t for t in out if t["day"] is not None]


def record_summary(trades: list[dict]) -> dict:
    """Win/loss tally over closed round trips.

    A win is P&L > 0 — what the trade actually made, not whether the bracket
    settled in the money: a position stopped out for a loss on a day the bracket
    later won is still a losing trade. Exact break-evens are counted separately as
    `pushes` so they don't inflate either side, and `win_rate` is over decided
    trades only (None with nothing decided yet).
    """
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] < 0)
    decided = wins + losses
    return {"trades": len(trades), "wins": wins, "losses": losses,
            "pushes": len(trades) - decided,
            "win_rate": (wins / decided) if decided else None,
            "realized": round(sum(t["pnl"] for t in trades), 4)}


def daily_pnl(trades: list[dict]) -> list[dict]:
    """Realized P&L per WEATHER day, oldest first. Bucketed by the day the
    position was about, not the timestamp it closed on — a position settling the
    next morning belongs to the day it traded."""
    totals: dict[date, float] = {}
    for t in trades:
        totals[t["day"]] = totals.get(t["day"], 0.0) + t["pnl"]
    return [{"date": d, "pnl": round(totals[d], 4)} for d in sorted(totals)]


def unrealized(open_marks: list[dict] | None) -> float | None:
    """Mark-to-market P&L of still-open positions, or None when none can be
    priced. `open_marks` rows are {entry_ask, count, bid} — the bid because that
    is the price a sale would actually fill into. A position with no live bid is
    skipped rather than marked at zero."""
    total, priced = 0.0, False
    for m in open_marks or []:
        bid, ask = m.get("bid"), m.get("entry_ask")
        if bid is None or ask is None:
            continue
        total += (bid - ask) * (m.get("count") or 1)
        priced = True
    return round(total, 4) if priced else None


def equity_curve(trades: list[dict], today: date,
                 open_marks: list[dict] | None) -> list[dict]:
    """Cumulative P&L from 0.0, oldest first, as [{"date", "total"}].

    Leads with an anchor point at 0.0 dated the day before the first point, so a
    single day of trading still draws a visible slope instead of one dot. Ends
    with a live point carrying open positions' unrealized P&L, folded into
    today's realized point when one already exists — appending would draw a
    second point on the same date.
    """
    curve, running = [], 0.0
    for row in daily_pnl(trades):
        running = round(running + row["pnl"], 4)
        curve.append({"date": row["date"], "total": running})

    unreal = unrealized(open_marks)
    if unreal is not None:
        live = {"date": today, "total": round(running + unreal, 4)}
        if curve and curve[-1]["date"] == today:
            curve[-1] = live
        else:
            curve.append(live)
    if not curve:
        return []
    return [{"date": curve[0]["date"] - timedelta(days=1), "total": 0.0}] + curve
