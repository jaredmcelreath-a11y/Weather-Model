"""The Screen page: brackets worth two minutes of attention.

Reads scan_candidates.jsonl and shows the newest firing. Display tables are
hand-rolled HTML because canvas-rendered st.dataframe cannot center cells --
the same reason every other table in this app is. Styling comes from
market_view's injected theme (.wtbl-wrap / .wtbl), which also gives the table
horizontal scroll on a phone.
"""
from __future__ import annotations

import html
import math
from datetime import date, datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

import bet_history
import market_view
import scan_cities
import scan_log
import screen_forecast
import screen_pnl
import screen_rules
from screen_rules import (MAX_LIVE_NO_PRICE, MIN_LIVE_NO_PRICE,  # noqa: F401
                          no_ask_of)
from sources import kalshi

# Hours before the market's close by which each variable's extreme has typically
# formed. A Kalshi day runs from 24 hours-to-close down to 0, so a low -- which
# bottoms out near dawn, roughly 7 hours in -- is settled once fewer than 17
# hours remain, while a high peaks mid-afternoon and needs all but ~8.
#
# This is diurnal typicality, NOT the calibrated lock detection the KDFW model
# uses. It is honest for a review screen and must not be treated as certainty:
# a 'dead' row, where realized temperature has actually ruled the bracket out,
# is the only hard evidence here.
_SETTLED_BELOW_HOURS = {"low": 17.0, "high": 8.0}

# The live NO-price band and its parser live in screen_rules: screen_alert
# applies the same gates on its 5-minute loop and cannot import this module
# (Streamlit). Why the band exists, in short — below the floor the market has
# already resolved the bracket and the screen's REFERENCE is what is wrong, not
# the price; above the cap the market agrees with the fade and 7c of upside for
# 93c of risk needs a ~93% strike rate to break even. Both gates matter more
# than they look: the first outcome scoring run put the mean cost of a flagged
# fade at 84.7%, i.e. near-favourites, where 83.4% of all brackets settle NO
# anyway. Unlike screen_rules' firing-time gates (MIN_CANDIDATE_PRICE /
# SETTLED_PRICE), this one acts on the price the trade would happen at: a
# bracket logged at 0.82 that has since drifted to 0.91 YES passes every
# firing-time gate and still reaches the table as a lost fade.

# How often the screen actually fires. Driven by an external cron-job.org
# repository_dispatch, NOT by the in-repo schedule: GitHub's own scheduler
# delivered 62% of the hourly slots on 2026-08-04 (9 of 15), median 23 min late,
# with gaps up to 3 hours -- the same finding log.yml records for its 30-min
# crons. The schedule in scan.yml is kept only as a free best-effort fallback.
#
# Everything the page says about staleness reads from this one constant, so a
# cadence change cannot leave a caption or tooltip quoting the old number.
FIRING_INTERVAL = timedelta(minutes=30)

_TIPS = {
    "Side": "Which side to buy. Always NO today: the screen only finds brackets "
            "priced ABOVE what the forecast supports, so the play is to fade.",
    "Ref": f"The reference the gap is measured from — the NWS forecast high/low "
           f"for that bracket's climate day, folded with any temperature "
           f"already realized, or for a 'dead' row the realized extreme alone. "
           f"Recomputed each firing, so up to "
           f"{int(FIRING_INTERVAL.total_seconds() // 60)} minutes old — unlike "
           f"'NO Now', which is live.",
    "Price": f"The YES price when the screen last fired, up to "
             f"{int(FIRING_INTERVAL.total_seconds() // 60)} minutes ago. What "
             f"the bracket cost to back then, not now — judge the trade on 'NO "
             f"Now', which is live.",
    "NO Now": "Live cost to buy NO, as a percent, fetched from Kalshi when this "
              "page loaded. This is the price of the trade the Side column "
              "names, and the one to judge it on. '—' means no live offer "
              "(often a market that has since closed).",
    "Gap": "Degrees F from the reference to the nearest edge of the bracket. A "
           "distance, NOT a probability — there is no per-city calibration here.",
    "Str": "How many multiples of the lead-adjusted bar the gap clears. The "
           "flat 4°F threshold ignores forecast lead, but measured error growth "
           "puts a day-ahead HIGH at 2.7× the same-day figure (a low at only "
           "1.16×). Below 1.0× the row qualified only because the threshold is "
           "lead-blind. Uses the error-growth RATIO alone — there is no "
           "per-city calibration here.",
    "Storm": "Chance of THUNDERSTORMS over the hours that can still move this "
             "extreme, from the same NWS forecast as Ref. A high's window ends "
             "at the forecast peak — no later storm can raise it; a low's runs "
             "to midnight, because an evening downdraft can still crash it. "
             "Read it as a caution on how much to trust Gap, not as a "
             "probability the bracket is wrong. '—' means no such hours left.",
    "Drift": "How the NWS forecast is verifying against the station right now, "
             "applied to Ref. '75→72' means the forecast is running 3°F hot at "
             "this hour, so if that error persists the real extreme is nearer "
             "72. A conditional, not a forecast — it assumes the current error "
             "holds until the extreme forms, which it may not. Gap and Str are "
             "NOT adjusted by it. '—' means no recent observation, or the "
             "extreme has already formed.",
    "Settled": "Whether that day's extreme has typically already formed, from "
               "the hours left and the variable. 'dead' rows are always Yes. "
               "Based on normal diurnal timing, not a lock detector.",
    "Hrs": "Hours until the Kalshi market closes, which is also the end of its "
           "climate day. Over 24 means the day has not started yet.",
    "Day": "Which climate day the bracket is about, in the city's own fixed "
           "standard time. Only 'Today' rows can turn red and only they send a "
           "phone alert — the alert loop runs every 5 minutes against the day "
           "already in progress, while a 'Tomorrow' row is an advance listing "
           "whose forecast has all day to move. '—' means the day could not be "
           "read.",
}

# The trade table's own tooltips. A SEPARATE map because half its column names
# also appear above with different meanings -- 'Side' there is the side to buy
# (always NO), here the side you actually took; 'Settled' there is whether the
# day's extreme has formed. One shared map would silently explain the wrong
# thing.
_TRADE_TIPS = {
    "Day": "The market's own climate day — the day the bracket is ABOUT, and "
           "exactly where the chart plots this trade. Sum a day's rows here and "
           "you get that day's step on the line. It is NOT when you bought: the "
           "screen lists brackets up to 30 hours out, so a bracket bought "
           "yesterday for today's market is filed under today.",
    "Side": "The side you actually hold. A fade off this screen is NO; a YES "
            "row is a bet the bracket hits.",
    "Entry": "Your average fill price for this position, from Kalshi's own "
             "record of your fills. Fees are not included here, but they ARE "
             "taken out of P&L.",
    "Exit": "What you got out at — your average sell price, or 100¢/0¢ for a "
            "position held to settlement. For a position still open it is the "
            "live BID (what you could exit at now), which is deliberately "
            "below the 'NO Now' ask above: that is what ENTERING costs.",
    "P&L": "Profit or loss in dollars, net of Kalshi fees once realized. A `~` "
           "value is an OPEN position marked to the live bid — nothing is "
           "realized until you sell or the market settles.",
    "% Gain": "This trade's P&L ÷ what you staked on it. A fade bought at 30¢ "
              "that settles pays +233%; one that misses is −100%.",
    "Qty": "Contracts held. Fractional sizes are Kalshi's own — it fills in "
           "cents of notional, not whole contracts.",
    "Result": "Won or Lost once realized, Sold if you closed it before "
              "settlement, Open while it is still running.",
    "Flagged": "Whether this screen's own log contains that bracket. Only the "
               "last three days of flags are loaded, so an older trade reads "
               "'—' — that means 'not checked', not 'never flagged'.",
}


def city_of(row: dict) -> str:
    """The row's city for display; the raw series ticker if it is unmapped."""
    return scan_cities.city_name(row.get("series") or "")


def side_of(row: dict) -> str:
    """The side to buy. Constant today — see _TIPS['Side']."""
    return "NO"


@st.cache_data(ttl=60, show_spinner=False)
def _live_markets(series: str) -> list:
    """Every open market under `series`, live from Kalshi. Cached ~60s so a
    rerun (theme toggle, nav click) does not re-hit the exchange."""
    return kalshi.list_series_markets(series, status="open")


def live_no_prices(rows: list, fetch=None) -> dict:
    """{ticker: NO ask in dollars} for these candidates, priced right now.

    The Price column is whatever the bracket cost at the last firing, up to
    eight hours ago; the decision is made at today's price, so the table shows
    both. One ladder call per distinct SERIES, not per bracket — a firing spans
    a handful of cities and each call returns that city's whole ladder.

    A series that fails is simply absent from the map: those rows lose their
    live price, the rest of the page is unaffected."""
    fetch = fetch or _live_markets
    wanted = {r.get("ticker") for r in rows}
    out = {}
    for series in dict.fromkeys(r.get("series") for r in rows):   # stable order
        if not series:
            continue
        try:
            markets = fetch(series)
        except Exception as e:            # noqa: BLE001 - a page must not crash
            print(f"[screen_view] {series}: live price skipped ({e})")
            continue
        for m in markets or []:
            if m.get("ticker") in wanted:
                price = no_ask_of(m)
                if price is not None:
                    out[m["ticker"]] = price
    return out


def tradeable_now(rows: list, live: dict):
    """(rows worth reviewing, n too cheap, n too expensive).

    A row is dropped only when it HAS a live NO price outside the band. A row
    with no live quote survives: an absent quote is thin liquidity or a market
    that has since closed, not evidence about the fade, and dropping it would
    hide the row for a reason the price never gave us.

    The two counts are returned separately because they mean opposite things —
    below the floor the market says the fade is WRONG, above the cap it agrees
    and has priced the edge away. Order is preserved for the caller's sort."""
    visible, cheap, dear = [], 0, 0
    for r in rows:
        price = live.get(r.get("ticker"))
        price = None if price is None else float(price)
        if price is not None and price < MIN_LIVE_NO_PRICE:
            cheap += 1
        elif price is not None and price > MAX_LIVE_NO_PRICE:
            dear += 1
        else:
            visible.append(r)
    return visible, cheap, dear


def hidden_notice(cheap: int, dear: int) -> str:
    """Why rows are missing from the table, or '' when none are.

    Both reasons are named rather than totalled: one combined count would hide
    whether the market is calling the screen wrong or simply agreeing with it.
    The count is always shown rather than the rows quietly disappearing -- an
    unexplained short table reads exactly like a screen that found nothing."""
    floor = round(MIN_LIVE_NO_PRICE * 100)
    cap = round(MAX_LIVE_NO_PRICE * 100)
    upside = round((1 - MAX_LIVE_NO_PRICE) * 100)
    parts = []
    if cheap:
        it = "it" if cheap == 1 else "them"
        parts.append(f"{cheap} hidden — live NO under {floor}%, the market has "
                     f"already resolved {it}.")
    if dear:
        lead = f"{dear} over {cap}%" if parts else f"{dear} hidden — live NO over {cap}%"
        parts.append(f"{lead}: {upside}¢ of upside for {cap}¢ of risk.")
    return " ".join(parts)


def _pct(price) -> str:
    """A dollar price as a whole percent, or an em dash when there is none."""
    return "—" if price is None else f"{round(float(price) * 100)}%"


# ---- Your open positions in screened brackets ------------------------------

def screened_by_ticker(rows: list) -> dict:
    """{ticker: its newest candidate row} across EVERY firing, not just the last.

    A bracket bought this morning often drops out of the current firing — its
    price climbed past the cap, or the forecast moved and closed the gap. Keying
    positions off the latest firing alone would erase a position from this table
    exactly when it started working, so the whole log is the membership test."""
    out = {}
    for r in sorted(rows, key=lambda r: r.get("ts") or ""):
        ticker = r.get("ticker")
        if ticker:
            out[ticker] = r                  # newest wins; the log is append-only
    return out


def _money(amount) -> str:
    """A signed dollar figure, with the app's true minus sign."""
    if amount is None:
        return "—"
    return f"+${amount:,.2f}" if amount >= 0 else f"−${abs(amount):,.2f}"


def _usd(amount) -> str:
    """An UNSIGNED dollar figure — for amounts that have no direction, like what
    a trade staked. `_money` would print it '+$18.56', as if staking were a gain."""
    return "—" if amount is None else f"${amount:,.2f}"


def _caption_safe(text: str) -> str:
    """A caption string with its dollar signs escaped.

    st.caption is markdown, and markdown reads a PAIR of '$' as inline LaTeX: a
    caption quoting two amounts renders the text between them in italic math
    ('+$2.27 on $18.56' became one equation). Escaping is the fix; the HTML
    tables are unaffected, which is why this is only needed here."""
    return text.replace("$", r"\$")


def _pct_signed(value) -> str:
    """A signed percent using the app's true minus sign, so a table's percents
    and its dollar figures agree on what a negative looks like."""
    return f"{value:+.1f}%".replace("-", "−")


def empty_notice(others: int) -> str:
    """Why the table is empty — never nothing at all.

    The first version of this section simply hid whenever it had no rows, which
    is how a portfolio feed blind to 38 of the 40 screened cities looked exactly
    like holding no positions. `others` is how many of your traded brackets this
    page does not cover (Dallas and Austin live on the History page)."""
    since = f"since {screen_pnl.SCREEN_START:%b %-d}"
    if not others:
        return f"No trades in screened brackets {since}."
    traded = "bracket" if others == 1 else "brackets"
    return (f"No trades in screened brackets {since} — {others} other {traded} "
            f"traded (Dallas and Austin are on the History page).")


def day_label(row: dict) -> str:
    """The day this trade is filed under — the market's own climate day, which is
    exactly the day the chart plots it on.

    NOT the fill timestamp, which is what this column used to show. Two problems
    with that: the screen lists brackets up to ~30h out, so a bracket bought Aug 3
    for the Aug 4 market read 'Aug 3' here while the chart counted it in Aug 4's
    step — the table and the chart could not be reconciled by eye — and `first_ts`
    is UTC, so an evening fill (7pm CDT = 00:00Z) already read a day late."""
    day = screen_pnl.weather_day(row)
    if day is None:
        first = row.get("first_ts")
        return first.strftime("%b %-d") if first else "—"
    return day.strftime("%b %-d")


def city_of_ticker(ticker: str) -> str:
    """The city a traded ticker belongs to, from its series prefix.

    Read off the TICKER rather than a candidate row: a trade older than the
    three days of flags this page loads has no candidate row, and it still has
    to say where it was."""
    return scan_cities.city_name((ticker or "").split("-")[0])


def contract_of(row: dict, cand: dict) -> str:
    """The bracket's wording — Kalshi's own where we have it.

    `build_rows` defaults `label` to the ticker when no market metadata was
    fetched, so that case falls back to the candidate row and then to the
    ticker's strike suffix ('T95'), which is at least identifiable."""
    label, ticker = row.get("label"), row.get("ticker") or ""
    if label and label != ticker:
        return str(label)
    if cand:
        return _bracket_label(cand)
    parts = ticker.split("-")
    return parts[-1] if len(parts) > 1 else ticker


def result_of(row: dict) -> str:
    """Won / Lost / Sold / Open.

    Deliberately NOT Kalshi's own 'yes'/'no' result, which the History page
    shows: nearly every trade here is a NO fade, so a settled winner would read
    'No' — the outcome of the bracket, the opposite of what happened to you."""
    if row.get("status") == "open":
        return "Open"
    if row.get("status") == "closed":
        return "Sold"
    pnl = row.get("pnl")
    if pnl is None:
        return "—"
    return "Won" if pnl > 0 else "Lost"


def flagged_of(ticker: str, screened: dict) -> str:
    """Whether the screen's own log has this bracket, over the days loaded.

    '—' means not in the loaded window, NOT 'never flagged' — the page reads
    three days of candidates. Shown because membership here is by city, so a bet
    the screen never listed can appear; leaving it invisible would let a manual
    trade quietly grade this screen's record."""
    return "Yes" if ticker in screened else "—"


def pct_gain_of(row: dict) -> str:
    """This trade's P&L as a percent of what it staked, or an em dash."""
    pnl, staked = screen_pnl.row_pnl(row), row.get("staked")
    if pnl is None or not staked:
        return "—"
    out = _pct_signed(100.0 * pnl / staked)
    return f"~{out}" if row.get("status") == "open" else out


def pnl_of(row: dict) -> str:
    """Dollars made or lost — prefixed '~' while the position is still open, so a
    mark is never mistaken for a realized number."""
    pnl = screen_pnl.row_pnl(row)
    out = _money(pnl)
    return f"~{out}" if row.get("status") == "open" and pnl is not None else out


def trade_display_rows(rows: list, screened: dict) -> list:
    """One display row per trade, newest first (the order `build_rows` gives).

    Open rows carry the `sopen` class: `_table` escapes every cell, so a tint has
    to come from the row, not an inline span."""
    out = []
    for r in rows:
        cand = screened.get(r.get("ticker")) or {}
        is_open = r.get("status") == "open"
        exit_at = r.get("current_value") if is_open else r.get("exit")
        out.append({
            "_class": "sopen" if is_open else "",
            "Day": day_label(r),
            "City": city_of_ticker(r.get("ticker")),
            "Contract": contract_of(r, cand),
            "Side": str(r.get("side") or "").upper(),
            "P&L": pnl_of(r),
            "% Gain": pct_gain_of(r),
            "Entry": market_view.cents(r.get("entry")),
            "Exit": market_view.cents(exit_at),
            "Qty": f"{float(r.get('qty') or 0):.2f}",
            "Result": result_of(r),
            "Flagged": flagged_of(r.get("ticker"), screened),
        })
    return out


def _day_subtotal(day: str, trades: list) -> dict:
    """One day's total, as a row of the same table.

    This exists because the page used to ask you to add its rows up yourself, and
    a day's total is exactly what the chart draws as that day's step. Two reports
    of "the chart says −11¢, the table says +5¢" both came down to arithmetic done
    by eye over rows that were not even contiguous."""
    graded = [r for r in trades if screen_pnl.row_pnl(r) is not None]
    total = sum(screen_pnl.row_pnl(r) for r in graded)
    staked = sum(r.get("staked") or 0.0 for r in graded)
    n_open = sum(1 for r in trades if r.get("status") == "open")
    money = _money(total)
    label = f"{len(trades)} trade" + ("" if len(trades) == 1 else "s")
    return {
        "_class": "ssub",
        "Day": f"{day} total",
        "Result": label + (f", {n_open} open" if n_open else ""),
        # '~' when any of it is a live mark, matching the per-trade rows.
        "P&L": f"~{money}" if n_open else money,
        "% Gain": _pct_signed(100.0 * total / staked) if staked else "—",
    }


def table_rows(trades: list, screened: dict) -> list:
    """Every trade as a display row, grouped by the day it is filed under, each
    day's block followed by its subtotal.

    Ordered by MARKET DAY (newest first), then by fill time within the day —
    NOT by fill time alone, which is what `build_rows` hands over. Those are
    different orderings once rows are dated by market day: a bracket bought Aug 3
    for the Aug 4 market sorted below the Aug 5 rows, splitting the Aug 4 group in
    two, and a reader taking the Aug 4 block as contiguous silently dropped it."""
    ordered = sorted(trades, reverse=True,
                     key=lambda r: (screen_pnl.weather_day(r) or date.min,
                                    r["first_ts"]))
    out, block, day = [], [], None
    for trade in ordered:
        label = day_label(trade)
        if day is not None and label != day:
            out.append(_day_subtotal(day, block))
            block = []
        day, block = label, block + [trade]
        out.extend(trade_display_rows([trade], screened))
    if block:
        out.append(_day_subtotal(day, block))
    return out


_RECON_COLUMNS = ["Day", "Bracket", "Bought", "Qty", "Entry", "Exit", "Gross",
                  "Fees", "Net"]


def reconciliation_rows(days: list) -> list:
    """Every day's step decomposed into the trades that produce it, gross → fees →
    net, with the day's own total beneath each block.

    The answer to "these two numbers disagree". `Gross` is what the Entry/Exit
    prices alone imply — what a hand-count produces — and `Fees` is Kalshi's cut,
    which appears nowhere else on the page but comes straight out of every P&L. A
    day the chart could not place says so instead of quietly differing."""
    out = []
    for day in days:
        label = day["day"].strftime("%b %-d") if day["day"] else "undated"
        for t in day["trades"]:
            bought = t["first_ts"]
            out.append({
                "Day": label,
                "Bracket": (t["ticker"] or "").split("-", 1)[-1],
                "Bought": bought.strftime("%b %-d %H:%MZ") if bought else "—",
                "Qty": f"{float(t['qty'] or 0):.2f}",
                "Entry": market_view.cents(t["entry"]),
                "Exit": market_view.cents(t["exit"]),
                "Gross": _money(t["gross"]),
                "Fees": "—" if not t["fee"] else f"−${t['fee']:,.2f}",
                "Net": _money(t["net"]) + ("" if t["realized"] else " (open)"),
            })
        mismatch = (day["step"] is not None
                    and abs(day["step"] - day["subtotal"]) > 0.005)
        if day["step"] is None:
            check = "not on the chart — undatable ticker"
        elif mismatch:
            check = f"chart step {_money(day['step'])} ≠ this total"
        else:
            check = f"chart step {_money(day['step'])} ✓"
        out.append({
            "_class": "ssub",
            "Day": f"{label} total",
            # Every money column on this row is the column's own sum, so the
            # arithmetic reads down the page; the chart's step sits beside it as
            # the cross-check rather than displacing one of the sums.
            "Bracket": check,
            "Gross": _money(sum(t["gross"] for t in day["trades"])),
            "Fees": "—" if not day["fees"] else f"−${day['fees']:,.2f}",
            "Net": _money(day["subtotal"]),
        })
    return out


def _render_reconciliation(trades: list) -> None:
    """Collapsed by default: the page's own arithmetic, for when a number is
    doubted. It stays on the page rather than living in a debugging session,
    because the same question came up twice and both times the evidence had to be
    reconstructed by hand."""
    days = screen_pnl.day_breakdown(trades, date.today())
    if not days:
        return
    with st.expander("Where each day's number comes from"):
        st.caption(
            "One line per trade: **Gross** is what the Entry and Exit prices "
            "alone imply — the figure a hand-count gives — and **Fees** is "
            "Kalshi's cut, which comes out of every P&L but appears nowhere "
            "else on this page. **Net** is Gross − Fees, and a day's Net is the "
            "step the chart draws. `Bought` is the fill time in UTC, which is "
            "why an evening trade can look like the next day."
        )
        st.markdown(_table(_RECON_COLUMNS, reconciliation_rows(days)),
                    unsafe_allow_html=True)


def earnings_caption(summary: dict) -> str:
    """One line under the chart: what the line is, and what is not yet real.

    Dollar signs are escaped — st.caption is markdown, which reads a pair of them
    as inline LaTeX and would render this whole sentence as an equation."""
    parts = [f"Cumulative P&L on screened-bracket trades since "
             f"{screen_pnl.SCREEN_START:%b %-d}, by the day each market is "
             f"about — {_money(summary['net_pnl'])} on "
             f"{_usd(summary['staked'])} staked."]
    if summary["n_open"]:
        held = "position" if summary["n_open"] == 1 else "positions"
        parts.append(f"The dashed stretch is live, not banked: {summary['n_open']} "
                     f"open {held} marked to the live bid "
                     f"({_money(summary['unrealized'])} unrealized), each on the "
                     f"day its own market resolves — so it moves with the market "
                     f"and a bracket bought for tomorrow sits on tomorrow.")
    if not summary["n_settled"]:
        parts.append("Nothing has settled yet, so none of it is realized.")
    return _caption_safe(" ".join(parts))


def strength_of(row: dict) -> str:
    """How many multiples of the lead-adjusted bar this row's gap clears.

    The flat 4F threshold means very different things at different leads —
    measured error growth puts a day-ahead HIGH at 2.7x the same-day figure —
    so a row can qualify purely because the threshold ignores lead. Below 1.0
    is exactly that row. Shown rather than filtered: the candidate log keeps
    them so screen_score can eventually say whether they ever win."""
    value = screen_rules.strength(row)
    return "—" if value is None else f"{value:.1f}×"


def storm_of(row: dict) -> str:
    """Thunderstorm chance as a whole percent, or an em dash.

    A row logged before this field existed has no `storm` key and reads as '—',
    the same as a row whose window has closed — both mean "nothing to say
    here", and neither is worth a log migration to distinguish."""
    value = row.get("storm")
    return "—" if value is None else f"{int(value)}%"


def drift_of(row: dict) -> str:
    """The reference, and where it lands if the forecast's current error holds.

    Both sides are rounded half-up to whole degrees because that is the basis
    Kalshi settles on -- which is why a row can read '72→72' beside a Ref column
    showing 71.6. The unrounded number stays visible in that column.

    '—' covers three cases that all mean "nothing to say": a row logged before
    this field existed, a dead row (whose Ref is realized fact, not a forecast),
    and a live row with no usable observation to anchor against."""
    ref, implied = row.get("forecast"), row.get("drift_ref")
    if ref is None or implied is None:
        return "—"
    return f"{math.floor(float(ref) + 0.5)}→{math.floor(float(implied) + 0.5)}"


def settled_of(row: dict) -> str:
    """'Yes' when that day's extreme has already formed, else 'No'."""
    if row.get("kind") == "dead":
        return "Yes"                     # realized temperature: hard evidence
    hours = row.get("hours_to_close")
    threshold = _SETTLED_BELOW_HOURS.get(row.get("variable"))
    if hours is None or threshold is None:
        return "No"
    return "Yes" if hours < threshold else "No"


def latest_firing(rows: list) -> list:
    """Only the candidates from the most recent firing."""
    if not rows:
        return []
    newest = max(r.get("ts") or "" for r in rows)
    return [r for r in rows if (r.get("ts") or "") == newest]


# One firing plus the scheduler's slack. Derived rather than written out, so
# changing the cadence cannot leave the highlight claiming rows are new a firing
# after they stopped being.
NEW_WINDOW = FIRING_INTERVAL + timedelta(minutes=15)


def _parse_ts(ts):
    """A candidate row's ISO timestamp as an aware datetime, or None."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def new_tickers(rows: list, now=None) -> set:
    """Brackets the latest firing ADDED — highlighted for their first hour.

    New means absent from the firing before this one, not merely present in the
    latest: with an hourly screen, most rows survive several firings and marking
    them all new would make the highlight meaningless. A bracket that drops off
    and comes back counts as new again, because it is news again.

    The whole set goes empty once the latest firing is older than an hour, so a
    missed cron or a page left open overnight stops claiming anything is fresh."""
    now = now or datetime.now(timezone.utc)
    stamps = sorted({r.get("ts") for r in rows if r.get("ts")})
    if not stamps:
        return set()
    when = _parse_ts(stamps[-1])
    if when is None or now - when > NEW_WINDOW:
        return set()
    previous = {r.get("ticker") for r in rows if r.get("ts") == stamps[-2]} \
        if len(stamps) > 1 else set()
    return {r.get("ticker") for r in rows if r.get("ts") == stamps[-1]} - previous


# ---- Which climate day a bracket settles on --------------------------------
#
# The screen lists brackets up to ~30 hours out, so roughly four rows in five
# are about TOMORROW. The alert loop deliberately pushes only the day already
# running (screen_alert.check), which left the red highlight promising a
# notification for rows that could never send one: measured over 2026-08-08/09,
# 69 of 84 newly-red rows were tomorrow's markets, 8 were today's and pushed,
# and not one same-day row in the price band was missed. Red now means exactly
# "this is on your phone too", and the Day column says which rows can be.

@st.cache_data(ttl=300, show_spinner=False)
def city_timezones() -> dict:
    """{series: IANA zone} from the reference screen.py publishes each firing.

    The same document screen_alert reads, so the page and the alert place a
    bracket's climate day identically. A city absent here is one the alert
    skips, which is why an unknown zone reads as "not today's" below rather
    than falling back to a guess."""
    doc = scan_log.read_doc(scan_log.REFERENCE_PATH)
    return {series: (info or {}).get("timezone")
            for series, info in (doc.get("cities") or {}).items()}


def _days(row: dict, zones: dict, now):
    """(the bracket's climate day, the day running now in its city), or Nones."""
    tzname = (zones or {}).get(row.get("series"))
    day = screen_forecast.climate_day_of_ticker(row.get("ticker") or "")
    if not tzname or day is None:
        return None, None
    return day, screen_forecast.in_progress_day(now or datetime.now(timezone.utc),
                                                tzname)


def settles_today(row: dict, zones: dict, now=None) -> bool:
    """Whether this bracket settles on the climate day running RIGHT NOW in its
    own city — exactly the rows screen_alert is able to push."""
    day, today = _days(row, zones, now)
    return day is not None and day == today


def day_of(row: dict, zones: dict, now=None) -> str:
    """'Today', 'Tomorrow', or '—' when the row cannot be placed.

    Only those two days can normally appear: Kalshi lists a temperature market
    about 30 hours before it closes. Anything else — an unmapped city, a ticker
    with no readable date, a market still open past its own day — reads '—'
    rather than inventing a third label for a case that means "nothing to say"."""
    day, today = _days(row, zones, now)
    if day is None:
        return "—"
    if day == today:
        return "Today"
    return "Tomorrow" if day == today + timedelta(days=1) else "—"


def pushed_tickers(rows: list, zones: dict, now=None) -> set:
    """Of these rows, the ones the alert loop would have notified about."""
    return {r.get("ticker") for r in rows if settles_today(r, zones, now)}


def display_rows(rows: list) -> list:
    """Soonest close first.

    Ordered by urgency rather than size of the apparent edge: a bracket three
    hours from settling needs a decision now, while a juicier one thirty hours
    out can wait for the next firing — and by then its price and the forecast
    will both have moved. A row with no hours sorts last rather than raising."""
    def rank(r):
        hours = r.get("hours_to_close")
        return float("inf") if hours is None else float(hours)
    return sorted(rows, key=rank)


def _bracket_label(row: dict) -> str:
    """Kalshi's own label when the row carries one; otherwise reconstructed.

    Rows logged before the label was captured fall back to floor/cap, which
    reads ">90" where Kalshi says "91° or above" -- correct as a strict
    inequality, but not what you see on their site."""
    label = row.get("label")
    if label:
        return str(label)
    floor, cap = row.get("floor"), row.get("cap")
    if floor is not None and cap is not None:
        return f"{floor}-{cap}"
    if cap is not None:
        return f"<{cap}"
    if floor is not None:
        return f">{floor}"
    return "?"


def _header_cell(label: str, tips: dict = None) -> str:
    """A <th>, with a tap-or-hover tooltip when the column needs explaining.

    Mirrors the app's .wxq/.wxqt pattern (focus for touch, hover for pointer)
    rather than a `title=` attribute, which phones cannot show at all."""
    tip = (_TIPS if tips is None else tips).get(label)
    if not tip:
        return f"<th>{html.escape(label)}</th>"
    return (f'<th class="stip-h">{html.escape(label)}'
            f'<span class="stip" tabindex="0" role="button" '
            f'aria-label="{html.escape(label)} info">?</span>'
            f'<span class="stipt">{html.escape(tip)}</span></th>')


_TIP_CSS = """
<style>
table.wtbl th.stip-h{position:relative;}
table.wtbl th .stip{display:inline-flex;align-items:center;justify-content:center;
 width:14px;height:14px;margin-left:4px;border-radius:50%;
 background:var(--surface);border:1px solid var(--border);color:var(--muted);
 font-size:10px;line-height:1;cursor:pointer;user-select:none;outline:none;
 vertical-align:middle;}
table.wtbl th .stipt{position:absolute;top:1.9rem;right:0;z-index:1000;
 width:max-content;max-width:min(19rem,74vw);padding:0.45rem 0.6rem;
 border-radius:8px;background:var(--surface2);border:1px solid var(--border);
 color:var(--ink);font-weight:400;font-size:0.76rem;line-height:1.35;
 text-align:left;white-space:normal;opacity:0;visibility:hidden;
 transition:opacity .12s ease;}
table.wtbl th:first-child .stipt{right:auto;left:0;}
table.wtbl th .stip:focus ~ .stipt{opacity:1;visibility:visible;}
@media (hover:hover){table.wtbl th .stip:hover ~ .stipt{opacity:1;visibility:visible;}}
/* Added by the newest firing AND settling today, so the phone alert carries the
   same row — reverts to ordinary ink an hour later. Tints the whole row,
   tracking the app's existing .hold red rather than a new colour. */
table.wtbl tr.snew td{color:var(--bad);background:rgba(229,120,110,0.12);}
/* Still-open trades in the history table, in the app's terracotta — the same
   colour the History page uses for a marked-to-market number, so a `~` value is
   visibly not a realized one. Whole-row, because _table escapes its cells and
   cannot carry an inline span. */
table.wtbl tr.sopen td{color:#C97B5E;}
/* A day's subtotal row: the same number the chart draws as that day's step. Set
   apart so it reads as a total rather than as one more trade. */
table.wtbl tr.ssub td{font-weight:700;background:var(--surface2);
 border-top:1px solid var(--border);}
</style>
"""

def _table(columns: list, rows: list, tips: dict = None) -> str:
    """A themed .wtbl table from display-string row dicts, headers tipped.

    A row may carry a `_class` marker (never a column) for the stylesheet."""
    head = "".join(_header_cell(c, tips) for c in columns)
    body = []
    for r in rows:
        cls = r.get("_class")
        body.append(f'<tr class="{html.escape(cls)}">' if cls else "<tr>")
        body.append("".join(f"<td>{html.escape(str(r.get(c, '')))}</td>"
                            for c in columns))
        body.append("</tr>")
    return ('<div class="wtbl-wrap"><table class="wtbl"><thead><tr>'
            + head + "</tr></thead><tbody>" + "".join(body)
            + "</tbody></table></div>")


# Order is a MOBILE decision: at 390px only the first five or so columns are
# visible before the wrap scrolls, so the ones a call actually turns on come
# first. Side is last precisely because it is constant (always NO today) and
# therefore the least informative cell on the row. Hrs sits further right than
# its importance suggests because rows are already SORTED by it — the ordering
# carries the urgency, the column just confirms it.
#
# Day is second, ahead of Var, and costs NO Now its place in the visible five on
# a phone. Worth it: four rows in five are about tomorrow, and until this column
# existed nothing on the page said so — the red highlight looked like it was
# skipping rows at random.
_COLUMNS = ["City", "Day", "Var", "Bracket", "Price", "NO Now", "Gap", "Str",
            "Storm", "Settled", "Drift", "Ref", "Hrs", "Side"]

# Same mobile logic as above, different priority: on a HISTORY table the outcome
# and the money are the point, so Result, P&L and % Gain sit ahead of the
# mechanics of the fill — eleven columns overflow even a desktop width, and
# Result was scrolling off the right edge where nobody would find it. Side is
# last for the same reason as the candidate table: it is NO on nearly every row.
_TRADE_COLUMNS = ["Day", "City", "Contract", "Result", "P&L", "% Gain",
                  "Entry", "Exit", "Qty", "Flagged", "Side"]


def _candidate_row(r: dict, live: dict, fresh: set, zones: dict = None,
                   now=None) -> dict:
    price = r.get("price")
    return {
        "_class": "snew" if r.get("ticker") in fresh else "",
        "City": city_of(r),
        "Day": day_of(r, zones or {}, now),
        "Var": str(r.get("variable") or ""),
        "Bracket": _bracket_label(r),
        "Price": "" if price is None else f"{float(price):.2f}",
        "NO Now": _pct(live.get(r.get("ticker"))),
        "Gap": r.get("gap"),
        "Str": strength_of(r),
        "Storm": storm_of(r),
        "Settled": settled_of(r),
        "Drift": drift_of(r),
        "Ref": r.get("forecast"),
        "Hrs": r.get("hours_to_close"),
        "Side": side_of(r),
    }


def track_record_caption(summary: dict, base) -> str:
    """One line on how the screen's flagged fades have actually settled.

    The base rate is always in the sentence. 83% of all brackets settle NO, so
    a hit rate quoted alone would flatter this screen enormously while proving
    nothing — what matters is the win rate against what the market CHARGED for
    the fade. Below MIN_SAMPLE the numbers are shown but the verdict is
    withheld, rather than letting three lucky rows read as a strategy."""
    if not summary.get("n"):
        return ("Track record: nothing has settled yet — flags resolve the "
                "morning after their climate day.")
    hit, implied = summary["hit_rate"], summary["mean_implied"]
    edge = summary["edge"]
    reference = "" if base is None else (
        f" All brackets settle NO {base:.1%} of the time, so only the edge "
        f"counts.")
    if not summary.get("enough"):
        return (f"Track record: {summary['n']} settled so far — too thin for a "
                f"verdict, no conclusion drawn.{reference}")
    return (f"Track record: {summary['n']} settled, won {hit:.1%} against a "
            f"{implied:.1%} price — edge {edge:+.1%} (±{summary['se']:.1%}), "
            f"{_money(summary['total_pnl'])} per contract staked.{reference}")


@st.cache_data(ttl=900, show_spinner=False)
def _settled_rows(days: int) -> list:
    """Kalshi's own settlement results, cached 15 min: a bracket settles once
    and the log only grows, so this does not need re-fetching per rerun."""
    return scan_log.load_recent(scan_log.SETTLED_PATH, days=days)


def _render_track_record(all_rows: list) -> None:
    """Whether the flags have been worth acting on. Never crashes the page: a
    missing settlement log means no track record, not a broken screen."""
    import screen_score
    try:
        settled = _settled_rows(7)
    except Exception as e:            # noqa: BLE001 - a page must not crash
        st.caption(f"Track record unavailable ({type(e).__name__}: {e}).")
        return
    records = screen_score.score(all_rows, settled)
    st.caption(track_record_caption(screen_score.summarize(records),
                                    screen_score.base_rate(settled)))


# The curve helpers live in bet_history: the History page draws the same shape
# from the same code, differing only in whether the line starts at the bankroll
# or at $0.
with_steps = bet_history.with_steps
line_parts = bet_history.line_parts


def earnings_chart(curve: list, color: str):
    """Cumulative-P&L line (x = weather day, y = dollars) with a dashed rule at
    break-even, on a transparent background so it follows the palette.

    Open positions are on the line, marked to the live bid on the day their own
    market resolves — drawn as a DASHED stretch with hollow points, so what is
    banked and what is only a mark are never the same line.

    Tap or click a point to pin its readout: touch devices never fire the hover
    events Vega tooltips need, the same reason the consensus and equity charts
    carry this pattern."""
    df = pd.DataFrame(with_steps(curve))
    # Bare date strings on a :T axis parse as UTC and render a day early for US
    # viewers; converting first keeps each point on its own day.
    df["date"] = pd.to_datetime(df["date"])
    labels = df.assign(label=df.apply(
        lambda r: f"{pd.to_datetime(r['date']).strftime('%b %-d')}\n"
                  f"{_money(r['step'])} that day\n"
                  f"{_money(r['total'])} total"
                  + (f"\n{_money(r['unrealized'])} still open" if r["open"]
                     else ""),
        axis=1))
    # Day granularity, explicitly: over a three-day span Vega otherwise picks
    # hourly ticks and labels a daily line '12 PM', '06 PM' — times at which
    # nothing on this chart ever happens.
    x = alt.X("date:T", title=None,
              axis=alt.Axis(format="%b %-d",
                            tickCount={"interval": "day", "step": 1}))
    y = alt.Y("total:Q", title="Cumulative P&L ($)",
              scale=alt.Scale(zero=False))
    realized, unrealized = (pd.DataFrame(part) for part in line_parts(curve))
    layers = []
    for part, dash in ((realized, None), (unrealized, [5, 4])):
        if len(part) < 2:            # a single point draws no segment
            continue
        part = part.assign(date=pd.to_datetime(part["date"]))
        mark = dict(strokeWidth=2.5, color=color)
        layers.append(alt.Chart(part).mark_line(
            **(mark if dash is None else dict(mark, strokeDash=dash))
        ).encode(x=x, y=y))

    pick = alt.selection_point(on="click", nearest=True, fields=["date"],
                               empty=False, clear="dblclick")
    # Hollow for a day whose step is still a live mark, solid for banked money —
    # `fill` is encodable where mark_point's `filled` is not, which is what makes
    # the two kinds of point distinguishable in one layer.
    dots = alt.Chart(df).mark_point(filled=False, opacity=1,
                                    strokeWidth=2.5).encode(
        x=x, y=y, stroke=alt.value(color),
        fill=alt.condition("datum.open", alt.value("transparent"),
                           alt.value(color)),
        size=alt.condition(pick, alt.value(150), alt.value(60)),
        tooltip=[alt.Tooltip("date:T", title="day"),
                 alt.Tooltip("step:Q", title="that day", format="$.2f"),
                 alt.Tooltip("total:Q", title="running total", format="$.2f"),
                 alt.Tooltip("unrealized:Q", title="still open", format="$.2f")],
    ).add_params(pick)
    pinned = alt.Chart(labels).mark_text(
        align="left", baseline="top", x=6, y=4, fontSize=13, fontWeight="bold",
        lineBreak="\n", lineHeight=15, color=color,
    ).encode(text="label:N").transform_filter(pick)
    rule = alt.Chart(pd.DataFrame({"y": [0.0]})).mark_rule(
        strokeDash=[4, 4], opacity=0.5).encode(y="y:Q")
    return (alt.layer(rule, *layers, dots, pinned)
            .properties(height=260, background="transparent")
            .configure_view(fill=None, strokeWidth=0))


@st.cache_data(ttl=3600, show_spinner=False)
def _market_meta(ticker: str) -> dict:
    """Kalshi's own label and strikes for one bracket.

    Cached an hour, per ticker: a bracket's wording never changes, and the
    alternative is a request per trade every time the 60-second trade cache turns
    over. A failure is NOT cached — it raises, and the caller drops that one
    label rather than pinning the error in place for an hour."""
    from sources import kalshi_portfolio
    return kalshi_portfolio.market_meta(ticker)


def _trade_meta(tickers) -> dict:
    """{ticker: market metadata} for the labels, best-effort per ticker."""
    out = {}
    for t in tickers:
        try:
            out[t] = _market_meta(t)
        except Exception as e:      # noqa: BLE001 - a label, not the row
            print(f"[screen_view] {t}: no market meta ({e})")
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _screen_trades():
    """(your screened-bracket trades, count of brackets traded elsewhere).

    `all_markets=True` is load-bearing: the default scoping drops every ticker
    outside the two stations this app models — 38 of the 40 cities screened here
    — which once made this whole section render nothing at all."""
    from sources import kalshi_portfolio
    start = screen_pnl.SCREEN_START
    fills = kalshi_portfolio.fills(start, all_markets=True)
    setts = kalshi_portfolio.settlements(start, all_markets=True)
    meta = _trade_meta({f["ticker"] for f in fills
                        if screen_pnl.is_screen_ticker(f["ticker"])})
    rows = screen_pnl.trade_rows(fills, setts, meta,
                                kalshi_portfolio.market_price)
    return rows, screen_pnl.other_tickers(fills)


def _render_history(all_rows: list) -> None:
    """What these trades have actually earned: the cumulative line, then every
    trade behind it.

    The heading always renders, with a caption saying why when there is no table
    under it: an empty section that could equally mean 'no trades', 'no creds' or
    'the feed cannot see this city' is unreadable, which is what hid the
    KDFW/KAUS scoping bug. Read-only; nothing here places an order."""
    screened = screened_by_ticker(all_rows)
    st.markdown("**Your Trades — Earnings History**")
    try:
        trades, others = _screen_trades()
    except Exception as e:          # noqa: BLE001 - a page must not crash
        st.caption(f"Kalshi portfolio unavailable ({type(e).__name__}: {e}).")
        return
    if not trades:
        st.caption(empty_notice(others))
        return

    summary = screen_pnl.summary(trades)
    with st.container(key="metrics2_screen_earnings"):
        cards = st.columns(4)
    _mc = market_view.metric_card
    cards[0].markdown(_mc(
        "Net P&L", _money(summary["net_pnl"]),
        f"Realized P&L on settled and sold trades ({_money(summary['realized_pnl'])}"
        f"), plus open positions marked to the live bid. Net of Kalshi fees."),
        unsafe_allow_html=True)
    cards[1].markdown(_mc(
        "Record (W–L)", f"{summary['wins']}–{summary['losses']}",
        "Settled and sold trades only — an open fade has not won yet."),
        unsafe_allow_html=True)
    cards[2].markdown(_mc(
        "Win rate", f"{summary['win_rate']:.0f}%",
        "Share of realized trades in profit. Remember 83% of ALL brackets settle "
        "NO, so a high hit rate on fades is not by itself an edge — the money "
        "figures are what decide it."), unsafe_allow_html=True)
    cards[3].markdown(_mc(
        "Avg % Return", f"{summary['roi']:+.1f}%",
        f"Stake-weighted: net P&L ÷ the {_money(summary['staked'])} staked. "
        f"Typical trade (median) {summary['median_trade_return']:+.1f}%."),
        unsafe_allow_html=True)

    curve = screen_pnl.earnings_curve(trades, date.today())
    if curve:
        st.altair_chart(earnings_chart(curve,
                                       market_view._chart_colors()["kalshi"]),
                        use_container_width=True)
    st.caption(earnings_caption(summary))
    st.markdown(_table(_TRADE_COLUMNS, table_rows(trades, screened),
                       _TRADE_TIPS), unsafe_allow_html=True)
    _render_reconciliation(trades)


def render() -> None:
    market_view._theme_controls()   # theme CSS + .wtbl/.wtbl-wrap + Settings
    st.subheader("Screen — Mispriced Brackets")
    st.caption(
        "Candidates for review, not signals. The NWS forecast is public, so a "
        "gap usually means the market knows something — 'dead' rows are the "
        "hard ones: realized temperature already ruled them out."
    )
    st.markdown(_TIP_CSS, unsafe_allow_html=True)   # header tips for both tables
    try:
        # Three days, not the whole history: enough for the newest firing, for
        # "what did the last firing add", and for the trade table's Flagged
        # column — Kalshi temperature markets close within ~30h of being listed.
        # A trade older than this window reads Flagged '—', which its tooltip
        # says means 'not checked' rather than 'never flagged'.
        all_rows = scan_log.load_recent(scan_log.CANDIDATES_PATH, days=3)
    except Exception as e:              # noqa: BLE001 - a page must not crash
        st.info(f"No candidate log yet ({e}).")
        return

    rows = latest_firing(all_rows)
    cheap = dear = 0
    if rows:
        live = live_no_prices(rows)
        rows, cheap, dear = tradeable_now(rows, live)
    if rows:
        zones = city_timezones()
        # Freshness is counted off the VISIBLE rows: a filtered-out arrival is
        # not on screen and must not be claimed in red. Same-day only, because
        # red's whole promise is that the alert loop pushed it — see the Day
        # column's tooltip.
        fresh = (new_tickers(all_rows) & {r.get("ticker") for r in rows}
                 & pushed_tickers(rows, zones))
        st.markdown(
            _table(_COLUMNS, [_candidate_row(r, live, fresh, zones)
                              for r in display_rows(rows)]),
            unsafe_allow_html=True)
        if fresh:
            st.caption(f"{len(fresh)} in red arrived with the latest firing "
                       f"and was pushed to your phone."
                       if len(fresh) == 1 else
                       f"{len(fresh)} in red arrived with the latest firing "
                       f"and were pushed to your phone.")
    else:
        # Still fall through to the positions table: holding something the
        # screen flagged yesterday is exactly the day you want to see it.
        st.info("No candidates in the latest firing.")
    if cheap or dear:
        st.caption(hidden_notice(cheap, dear))
    _render_track_record(all_rows)
    _render_history(all_rows)
