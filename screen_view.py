"""The Screen page: brackets worth two minutes of attention.

Reads scan_candidates.jsonl and shows the newest firing. Display tables are
hand-rolled HTML because canvas-rendered st.dataframe cannot center cells --
the same reason every other table in this app is. Styling comes from
market_view's injected theme (.wtbl-wrap / .wtbl), which also gives the table
horizontal scroll on a phone.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

import streamlit as st

import market_view
import scan_cities
import scan_log
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

_TIPS = {
    "Side": "Which side to buy. Always NO today: the screen only finds brackets "
            "priced ABOVE what the forecast supports, so the play is to fade.",
    "Ref": "The reference the gap is measured from — the NWS forecast high/low "
           "for that bracket's climate day, or for a 'dead' row the temperature "
           "already realized.",
    "Price": "The YES price when the screen last fired, which can be hours old "
             "— the screen runs three times a day. What the bracket cost to "
             "back then, not now.",
    "NO Now": "Live cost to buy NO, as a percent, fetched from Kalshi when this "
              "page loaded. This is the price of the trade the Side column "
              "names, and the one to judge it on. '—' means no live offer "
              "(often a market that has since closed).",
    "Gap": "Degrees F from the reference to the nearest edge of the bracket. A "
           "distance, NOT a probability — there is no per-city calibration here.",
    "Settled": "Whether that day's extreme has typically already formed, from "
               "the hours left and the variable. 'dead' rows are always Yes. "
               "Based on normal diurnal timing, not a lock detector.",
    "Hrs": "Hours until the Kalshi market closes, which is also the end of its "
           "climate day. Over 24 means the day has not started yet.",
    "Entry": "Your average fill price for this position, from Kalshi's own "
             "record of your fills. Fees are not included.",
    "Now": "What the position is worth right now, marked to the live BID — the "
           "price you could exit at. Deliberately lower than the 'NO Now' ask "
           "above, which is what ENTERING costs.",
    "Unreal P&L": "Open profit or loss at that mark: qty × (now − entry). "
                  "Nothing is realized until you sell or the market settles.",
}


def city_of(row: dict) -> str:
    """The row's city for display; the raw series ticker if it is unmapped."""
    return scan_cities.city_name(row.get("series") or "")


def side_of(row: dict) -> str:
    """The side to buy. Constant today — see _TIPS['Side']."""
    return "NO"


def no_ask_of(market: dict):
    """Dollars to BUY NO on this market right now, or None when unquoted.

    Kalshi's own NO ask when there is one, else the YES bid inverted: buying NO
    sells against the resting YES bid, so NO ask = 1 - yes bid. Prices arrive as
    dollar STRINGS ("0.8800"), the gotcha that silently empties a scan pass."""
    ask = scan_log._dollars(market.get("no_ask_dollars"))
    if ask is not None:
        return ask
    bid = scan_log._dollars(market.get("yes_bid_dollars"))
    return None if bid is None else round(1.0 - bid, 2)


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


def open_screened(positions: list, screened: dict) -> list:
    """The open positions held in brackets the screen has flagged."""
    return [p for p in positions if p.get("ticker") in screened]


def build_positions(fills: list, settlements: dict, mark) -> list:
    """Still-open positions from raw fills, each marked at `mark(ticker, side)`.

    Deliberately NOT market_view._open_positions: that goes through
    kalshi_portfolio.fills() at its default scoping, which drops every ticker
    outside the two stations this app models — i.e. 38 of the 40 cities this
    page screens. Anything bought off this screen in Denver or Philadelphia was
    filtered out three layers down and the table simply rendered nothing.

    Market metadata is left empty: label, city and variable all come from the
    candidate row, so there is no reason to spend a request per ticker on it."""
    import bet_history                 # lazy: pulls the signing dependency
    out = []
    for r in bet_history.build_rows(fills, settlements, {}):
        if r["status"] != "open":
            continue
        out.append({**r, "current_value": mark(r["ticker"], r["side"])})
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _portfolio_positions() -> list:
    """Your open positions across EVERY Kalshi city, marked to the live bid."""
    import bet_history
    from sources import kalshi_portfolio
    start = bet_history.BETS_START
    return build_positions(
        kalshi_portfolio.fills(start, all_markets=True),
        kalshi_portfolio.settlements(start, all_markets=True),
        kalshi_portfolio.market_price)


def empty_notice(positions: list) -> str:
    """Why the table is empty — never nothing at all.

    The first version of this section simply hid whenever it had no rows, which
    is how a portfolio feed blind to 38 of the 40 screened cities looked exactly
    like holding no positions."""
    if not positions:
        return "No open positions."
    return (f"No open positions in a flagged bracket "
            f"({len(positions)} open elsewhere).")


def unrealized(position: dict):
    """Dollars of open P&L: qty x (mark - entry), or None when unpriced."""
    mark, entry, qty = (position.get("current_value"), position.get("entry"),
                        position.get("qty"))
    if mark is None or entry is None or qty is None:
        return None
    return qty * (mark - entry)


def total_unrealized(positions: list):
    """Open P&L across `positions`, counting only the ones that have a mark."""
    return sum(u for u in (unrealized(p) for p in positions) if u is not None)


def _money(amount) -> str:
    """A signed dollar figure, with the app's true minus sign."""
    if amount is None:
        return "—"
    return f"+${amount:,.2f}" if amount >= 0 else f"−${abs(amount):,.2f}"


def position_rows(positions: list, screened: dict) -> list:
    """Display rows pairing what each position cost against what it is worth now.

    City, variable and label come from the CANDIDATE row rather than the
    portfolio feed's market metadata: the feed's `variable` is derived from the
    two stations this app models (KDFW/KAUS) and is None for the other 38 cities
    the screen covers, while the candidate row already carries all three."""
    out = []
    for p in positions:
        cand = screened.get(p.get("ticker")) or {}
        out.append({
            "City": city_of(cand) if cand else (p.get("ticker") or ""),
            "Contract": _bracket_label(cand) if cand else p.get("label", ""),
            "Side": str(p.get("side") or "").upper(),
            "Qty": f"{float(p.get('qty') or 0):.2f}",
            "Entry": market_view.cents(p.get("entry")),
            "Now": market_view.cents(p.get("current_value")),
            "Unreal P&L": _money(unrealized(p)),
        })
    return out


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


NEW_WINDOW = timedelta(hours=1)     # one firing: the screen now runs hourly


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


def _header_cell(label: str) -> str:
    """A <th>, with a tap-or-hover tooltip when the column needs explaining.

    Mirrors the app's .wxq/.wxqt pattern (focus for touch, hover for pointer)
    rather than a `title=` attribute, which phones cannot show at all."""
    tip = _TIPS.get(label)
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
/* Added by the newest firing — reverts to ordinary ink an hour later. Tints the
   whole row, tracking the app's existing .hold red rather than a new colour. */
table.wtbl tr.snew td{color:var(--bad);background:rgba(229,120,110,0.12);}
</style>
"""

def _table(columns: list, rows: list) -> str:
    """A themed .wtbl table from display-string row dicts, headers tipped.

    A row may carry a `_class` marker (never a column) for the stylesheet."""
    head = "".join(_header_cell(c) for c in columns)
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
_COLUMNS = ["City", "Var", "Bracket", "Price", "NO Now", "Gap", "Settled",
            "Ref", "Hrs", "Side"]

_POSITION_COLUMNS = ["City", "Contract", "Side", "Qty", "Entry", "Now",
                     "Unreal P&L"]


def _candidate_row(r: dict, live: dict, fresh: set) -> dict:
    price = r.get("price")
    return {
        "_class": "snew" if r.get("ticker") in fresh else "",
        "City": city_of(r),
        "Var": str(r.get("variable") or ""),
        "Bracket": _bracket_label(r),
        "Price": "" if price is None else f"{float(price):.2f}",
        "NO Now": _pct(live.get(r.get("ticker"))),
        "Gap": r.get("gap"),
        "Settled": settled_of(r),
        "Ref": r.get("forecast"),
        "Hrs": r.get("hours_to_close"),
        "Side": side_of(r),
    }


def _render_positions(all_rows: list) -> None:
    """What you actually hold in brackets this screen has flagged.

    The heading always renders, with a caption saying why when there is no
    table under it: an empty section that could equally mean 'no positions',
    'no creds' or 'the feed cannot see this city' is unreadable, which is what
    hid the KDFW/KAUS scoping bug. Read-only; nothing here places an order."""
    import bet_history            # lazy: pulls the cryptography-backed portfolio
    screened = screened_by_ticker(all_rows)
    st.markdown("**Your Open Positions**")
    try:
        held = _portfolio_positions()
    except Exception as e:          # noqa: BLE001 - a page must not crash
        st.caption(f"Kalshi portfolio unavailable ({type(e).__name__}: {e}).")
        return
    positions = open_screened(held, screened)
    if not positions:
        st.caption(empty_notice(held))
        return
    rows = sorted(position_rows(positions, screened),
                  key=lambda r: (r["City"], r["Contract"]))
    st.caption(
        f"{len(rows)} open in brackets the screen has flagged, marked to the "
        f"live bid — unrealized {_money(total_unrealized(positions))}. Fills "
        f"before {bet_history.BETS_START:%b %-d} are outside the current "
        "history window and will not appear."
    )
    st.markdown(_table(_POSITION_COLUMNS, rows), unsafe_allow_html=True)


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
        all_rows = scan_log.load(scan_log.CANDIDATES_PATH)
    except Exception as e:              # noqa: BLE001 - a page must not crash
        st.info(f"No candidate log yet ({e}).")
        return

    rows = latest_firing(all_rows)
    if rows:
        live = live_no_prices(rows)
        fresh = new_tickers(all_rows)
        st.markdown(
            _table(_COLUMNS, [_candidate_row(r, live, fresh)
                              for r in display_rows(rows)]),
            unsafe_allow_html=True)
        if fresh:
            st.caption(f"{len(fresh)} in red arrived with this hour's firing.")
    else:
        # Still fall through to the positions table: holding something the
        # screen flagged yesterday is exactly the day you want to see it.
        st.info("No candidates in the latest firing.")
    _render_positions(all_rows)
