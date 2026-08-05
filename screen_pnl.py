"""What the Screen's fades have actually EARNED.

`screen_score` measures the flags per contract — did a bracket the screen listed
settle NO, against what the market charged. This module measures the money: the
user's real Kalshi fills in screened brackets, their realized P&L, and the
cumulative earnings curve since the first one.

WHICH TRADES COUNT: every weather bet on a screened city that is NOT a city this
app models itself (Dallas/Austin, which have the History page). Membership is by
city rather than by "the screen logged this bracket" because the candidate log's
first firing is 2026-08-04T01:04Z — Aug 3 ~8pm CDT — and a Denver bracket bought
earlier that day is simply not in it. A log-membership test would drop the very
first trade. The Screen page shows a `Flagged` column so a bet the screen never
listed is visible rather than hidden.

Assembly (episode splitting, the dominant-side rule, sold-out-before-settlement,
fees) is delegated to `bet_history.build_rows`: a second P&L implementation would
be a second truth to keep in sync with the one real money runs through.

Pure — no Streamlit, no network, no clock beyond what callers pass in.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import median

import bet_history
import scan_cities

# The first fade off this screen (a Denver bracket). Fills before it are not
# fetched or shown. The one date constant here — everything reads it.
SCREEN_START = date(2026, 8, 3)


def is_screen_ticker(ticker) -> bool:
    """Whether this Kalshi ticker is one of the screen's own trades.

    Two conditions: the series is one of the 40 the screen covers, and it is not
    a station this app forecasts itself. The second is read from config via
    `bet_history.ticker_station`, so a third modeled city drops off this page the
    day it is added there rather than double-counting against History."""
    prefix = (ticker or "").split("-")[0]
    if not scan_cities.is_screened_series(prefix):
        return False
    return bet_history.ticker_station(ticker) is None


def trade_rows(fills: list, settlements: dict, meta: dict, mark) -> list:
    """Every screened-bracket position from raw fills, newest first.

    Open rows carry `current_value` from `mark(ticker, side)` — the live bid, what
    the position could be exited at — so their P&L can be marked to market. A mark
    that raises leaves that one row unpriced instead of emptying the table: an
    unquotable ticker is common (a market that has since closed) and must not cost
    the other rows their history."""
    rows = [r for r in bet_history.build_rows(fills, settlements, meta)
            if is_screen_ticker(r["ticker"])]
    for r in rows:
        if r["status"] != "open":
            continue
        try:
            r["current_value"] = mark(r["ticker"], r["side"])
        except Exception as e:            # noqa: BLE001 - one dead quote, not a page
            print(f"[screen_pnl] {r['ticker']}: no mark ({e})")
            r["current_value"] = None
    return rows


def other_tickers(fills: list) -> int:
    """How many DISTINCT traded brackets this page does not cover.

    The diagnostic behind the empty-table caption: "no screened trades" and "the
    feed cannot see them" look identical without it, which is how the KDFW/KAUS
    scoping bug went unnoticed in the section this replaced."""
    return len({f.get("ticker") for f in fills or []
                if not is_screen_ticker(f.get("ticker"))})


def row_pnl(row: dict):
    """A trade's P&L: realized once settled or sold, else marked to the live bid.
    None when an open position has no mark yet."""
    return bet_history._pnl_mtm(row)


def weather_day(row: dict, today=None):
    """The day a row's market is ABOUT — delegated to `bet_history.row_day`, which
    is THE dating rule for every view that shows or sums a day's P&L."""
    return bet_history.row_day(row, today)


def earnings_curve(rows: list, today) -> list:
    """Cumulative P&L from $0, one point per weather day, oldest first.

    From zero rather than a bankroll: these fades share the one Kalshi account, so
    any "starting balance" for them alone would be invented — the honest line is
    money made and lost. Everything else (weather-day bucketing, open positions
    marked on their own day, the `unrealized`/`open` flags the dashed stretch reads,
    the $0 anchor) is `bet_history.curve`, which the History page draws from the
    same code with the bankroll as its base."""
    return bet_history.curve(rows, today, base=0.0)


def day_breakdown(rows: list, today) -> list:
    """Every day's number with the trades behind it — see `bet_history`."""
    return bet_history.day_breakdown(rows, today)


def open_unrealized(rows: list) -> float:
    """Live unrealized P&L of open positions: qty × (mark − entry). Rows without a
    mark are skipped, not counted as flat."""
    return bet_history.open_unrealized(rows)


def summary(rows: list) -> dict:
    """Headline numbers for the metric cards.

    Wins/losses/win-rate are REALIZED only — an open fade has not won yet. The
    money figures are marked to market, so Net P&L moves with the open positions
    the way Kalshi's own portfolio total does."""
    realized = [r for r in rows if r["status"] in ("settled", "closed")]
    wins = sum(1 for r in realized if r["pnl"] > 0)
    losses = sum(1 for r in realized if r["pnl"] <= 0)
    graded = [r for r in rows if row_pnl(r) is not None]
    net_pnl = sum(row_pnl(r) for r in graded)
    staked = sum(r["staked"] for r in graded)
    # Median of each trade's own % return, not the mean: fading favorites pairs
    # many small wins with rare −100% losses, and their mean washes to ~0 even
    # when the strategy is making money.
    per_trade = [100.0 * row_pnl(r) / r["staked"] for r in graded if r["staked"]]
    return {
        "n_settled": len(realized),
        "wins": wins,
        "losses": losses,
        "win_rate": (100.0 * wins / len(realized)) if realized else 0.0,
        "net_pnl": net_pnl,
        "realized_pnl": sum(r["pnl"] for r in realized),
        "unrealized": open_unrealized(rows),
        "staked": staked,
        "roi": (100.0 * net_pnl / staked) if staked else 0.0,
        "median_trade_return": median(per_trade) if per_trade else 0.0,
        "n_open": sum(1 for r in rows if r["status"] == "open"),
    }
