"""The Screen page: brackets worth two minutes of attention.

Reads scan_candidates.jsonl and shows the newest firing. Display tables are
hand-rolled HTML because canvas-rendered st.dataframe cannot center cells --
the same reason every other table in this app is. Styling comes from
market_view's injected theme (.wtbl-wrap / .wtbl), which also gives the table
horizontal scroll on a phone.
"""
from __future__ import annotations

import html

import streamlit as st

import market_view
import scan_cities
import scan_log

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
    "Gap": "Degrees F from the reference to the nearest edge of the bracket. A "
           "distance, NOT a probability — there is no per-city calibration here.",
    "Settled": "Whether that day's extreme has typically already formed, from "
               "the hours left and the variable. 'dead' rows are always Yes. "
               "Based on normal diurnal timing, not a lock detector.",
    "Hrs": "Hours until the Kalshi market closes, which is also the end of its "
           "climate day. Over 24 means the day has not started yet.",
}


def city_of(row: dict) -> str:
    """The row's city for display; the raw series ticker if it is unmapped."""
    return scan_cities.city_name(row.get("series") or "")


def side_of(row: dict) -> str:
    """The side to buy. Constant today — see _TIPS['Side']."""
    return "NO"


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
</style>
"""

# Order is a MOBILE decision: at 390px only the first five or so columns are
# visible before the wrap scrolls, so the ones a call actually turns on come
# first. Side is last precisely because it is constant (always NO today) and
# therefore the least informative cell on the row. Hrs sits further right than
# its importance suggests because rows are already SORTED by it — the ordering
# carries the urgency, the column just confirms it.
_COLUMNS = ["City", "Var", "Bracket", "Price", "Gap", "Settled", "Ref",
            "Hrs", "Side"]


def render() -> None:
    market_view._theme_controls()   # theme CSS + .wtbl/.wtbl-wrap + Settings
    st.subheader("Screen — Mispriced Brackets")
    st.caption(
        "Candidates for review, not signals. The NWS forecast is public, so a "
        "gap usually means the market knows something — 'dead' rows are the "
        "hard ones: realized temperature already ruled them out."
    )
    try:
        rows = latest_firing(scan_log.load(scan_log.CANDIDATES_PATH))
    except Exception as e:              # noqa: BLE001 - a page must not crash
        st.info(f"No candidate log yet ({e}).")
        return
    if not rows:
        st.info("No candidates in the latest firing.")
        return

    head = "".join(_header_cell(c) for c in _COLUMNS)
    body = []
    for r in display_rows(rows):
        price = r.get("price")
        body.append(
            "<tr>"
            f"<td>{html.escape(city_of(r))}</td>"
            f"<td>{html.escape(str(r.get('variable') or ''))}</td>"
            f"<td>{html.escape(_bracket_label(r))}</td>"
            f"<td>{'' if price is None else f'{float(price):.2f}'}</td>"
            f"<td>{r.get('gap')}</td>"
            f"<td>{html.escape(settled_of(r))}</td>"
            f"<td>{r.get('forecast')}</td>"
            f"<td>{r.get('hours_to_close')}</td>"
            f"<td>{html.escape(side_of(r))}</td>"
            "</tr>")
    st.markdown(
        _TIP_CSS + '<div class="wtbl-wrap"><table class="wtbl"><thead><tr>'
        + head + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>",
        unsafe_allow_html=True)
