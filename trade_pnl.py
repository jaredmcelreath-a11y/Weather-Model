"""Turn the trader's audit log into realized trades, daily P&L, and an equity
curve that starts at zero.

Pure — every function takes plain records and returns plain data, so the shadow
run can be scored without any network or Streamlit. IO lives in trade_state
(reading the log) and trade_view (rendering).

Deliberately separate from `bet_history`'s equity curve: that one is anchored to
a real STARTING_BANKROLL and bound to the Kalshi fill schema. This one measures a
strategy from 0, over the trader's own entry/exit records.

Records predating the 2026-07-28 schema change carry no `exit_price`, so their
round trips are unscorable. They are SKIPPED, never counted as zero — a missing
price is not a break-even trade.
"""
from __future__ import annotations

from datetime import date, timedelta

import trader


def _day_of(rec: dict) -> date | None:
    """The weather day a record belongs to — its recorded `day`, else the event
    date in its ticker. Reuses the trader's own resolution so the page and the
    loop can never disagree about which day a position belongs to."""
    return trader._position_day(rec)


def closed_trades(records: list[dict]) -> list[dict]:
    """Completed round trips, oldest first.

    Pairs each `entry` with the next `exit` on the same ticker, so a bracket that
    is entered, stopped out, and re-entered yields two trades rather than one.
    Entries still open, and pairs whose exit predates the `exit_price` schema, are
    skipped.
    """
    open_by_ticker: dict[str, list[dict]] = {}
    out = []
    for rec in sorted(records, key=lambda r: r.get("ts") or ""):
        kind, tkr = rec.get("kind"), rec.get("ticker")
        if not tkr:
            continue
        if kind == "entry":
            open_by_ticker.setdefault(tkr, []).append(rec)
        elif kind == "exit":
            queue = open_by_ticker.get(tkr) or []
            if not queue:
                continue                      # exit with no matching entry
            entry = queue.pop(0)
            price = rec.get("exit_price")
            if price is None:
                continue                      # pre-schema: unscorable, not zero
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
