"""Shared rendering for the KDFW high/low market dashboard pages.

One render path, parameterized by a `MarketAdapter` (see markets.py), so the
Robinhood (ForecastEx) and Kalshi pages stay in sync. Everything market-specific
— the live contract fetch, the model→contract price mapping, and the on-screen
wording — comes from the adapter; all trade logic (edge signals, flip-prob, exit
plans, Top-3 flip/hold, Safest-hold) is identical across exchanges.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

import calibration
import model
from config import STATION_ID, TIMEZONE

_TZ = ZoneInfo(TIMEZONE)

# Most the "Safest hold to $1" box will pay for a contract. Above this the price
# is already near settlement value, so the remaining upside doesn't justify the
# capital — exclude it from the safe-hold pick regardless of edge.
SAFE_HOLD_MAX_ASK = 0.92

# ---------------------------------------------------------------------------
# Dark theme — a light serif palette on a dark ground, modeled on the agency
# site (IM Fell English headings + Bitter body). Two palettes, switchable at
# runtime via the "Settings" control; .streamlit/config.toml seeds the first
# paint with the Deep slate values so there's no white flash before this runs.
# ---------------------------------------------------------------------------
THEMES = {
    "Deep slate": {
        "bg": "#14181C", "surface": "#1E252B", "surface2": "#283037",
        "ink": "#E8ECEF", "muted": "#9BA7B0", "accent": "#6FBF9A",
        "accent_strong": "#97D6B8", "border": "rgba(232,236,239,0.12)",
        "good": "#7FD3A2", "warn": "#E4C878", "bad": "#E59A8E",
    },
    "Charcoal": {
        "bg": "#201B18", "surface": "#2B2420", "surface2": "#37302A",
        "ink": "#F7F1E6", "muted": "#B7A99A", "accent": "#7FB79A",
        "accent_strong": "#A6D2BC", "border": "rgba(231,219,201,0.16)",
        "good": "#8FD3A6", "warn": "#E7C67A", "bad": "#E7A99B",
    },
}
DEFAULT_THEME = "Charcoal"


def _inject_theme(name):
    """Emit the <style> block that paints the whole app in palette `name`."""
    t = THEMES.get(name, THEMES[DEFAULT_THEME])
    st.markdown(
        "<style>\n"
        "@import url('https://fonts.googleapis.com/css2?family=IM+Fell+English:ital@0;1"
        "&family=Bitter:wght@400;500;600;700&display=swap');\n"
        ":root{"
        f"--bg:{t['bg']};--surface:{t['surface']};--surface2:{t['surface2']};"
        f"--ink:{t['ink']};--muted:{t['muted']};--accent:{t['accent']};"
        f"--accent-strong:{t['accent_strong']};--border:{t['border']};"
        f"--good:{t['good']};--warn:{t['warn']};--bad:{t['bad']};}}\n"
        ".stApp{background-color:var(--bg)!important;}\n"
        ".stApp,.stApp p,.stApp li,.stApp label,[data-testid=\"stMarkdownContainer\"]"
        "{color:var(--ink);font-family:'Bitter',Georgia,serif;}\n"
        "h1,h2,h3,h4,[data-testid=\"stHeading\"]"
        "{font-family:'IM Fell English',Georgia,serif!important;letter-spacing:-0.01em;}\n"
        "[data-testid=\"stSidebar\"]{background-color:var(--surface)!important;}\n"
        # push the whole page up (trim Streamlit's tall default top padding)
        "[data-testid=\"stMainBlockContainer\"]{padding-top:2.5rem!important;}\n"
        # pin the sidebar 'Settings' expander to the bottom, a bit above the edge
        "[data-testid=\"stSidebarUserContent\"]{display:flex;flex-direction:column;"
        "min-height:calc(100vh - 5rem);}\n"
        "[data-testid=\"stSidebar\"] [data-testid=\"stExpander\"]"
        "{margin-top:auto;margin-bottom:1.5rem;}\n"
        "[data-testid=\"stMetric\"]{background:var(--surface);border:1px solid var(--border);"
        "border-radius:12px;padding:0.7rem 0.9rem;text-align:center;align-items:center;"
        "position:relative;}\n"
        # center the label/value and let the wider ones wrap fully rather than clip
        "[data-testid=\"stMetricLabel\"]{display:flex!important;"
        "justify-content:center!important;align-items:center;width:100%!important;"
        "padding:0 0.4rem;overflow:visible!important;}\n"
        # pin the '?' help bubble's inline wrapper (<label>) to the top-right corner
        # of the metric box so it's fully out of the centered label text's way
        "[data-testid=\"stMetricLabel\"] label,[data-testid=\"stMetricLabel\"] .e1gk92lc3"
        "{position:absolute!important;top:5px;right:7px;margin:0!important;}\n"
        "[data-testid=\"stMetricLabel\"],[data-testid=\"stMetricLabel\"] *"
        "{white-space:normal!important;overflow:visible!important;text-overflow:clip!important;"
        "font-weight:700;color:var(--muted);font-size:0.76rem;text-align:center!important;}\n"
        "[data-testid=\"stMetricValue\"]{font-size:1.55rem;white-space:normal;"
        "overflow-wrap:anywhere;justify-content:center;text-align:center;}\n"
        # phones: grid the 6 top metric boxes 2-per-row instead of stacking them
        # one-per-row (desktop keeps the 6-across row — this only fires ≤640px).
        "@media (max-width:640px){"
        ".st-key-top_metrics [data-testid=\"stHorizontalBlock\"]"
        "{flex-wrap:wrap!important;gap:0.8rem!important;}"
        ".st-key-top_metrics [data-testid=\"stColumn\"]"
        "{flex:1 1 47%!important;min-width:47%!important;width:47%!important;}"
        # keep the High/Low Consensus/Spread/Resolved trio on one row on phones
        "[class*=\"st-key-mini_\"] [data-testid=\"stHorizontalBlock\"]"
        "{flex-wrap:nowrap!important;gap:0.35rem!important;}"
        "[class*=\"st-key-mini_\"] [data-testid=\"stColumn\"]"
        "{flex:1 1 33%!important;min-width:0!important;width:33%!important;}"
        # shrink the mini-metric text a touch so the trio fits comfortably on phones
        "[class*=\"st-key-mini_\"] [data-testid=\"stMetricValue\"]{font-size:1.1rem!important;}"
        "[class*=\"st-key-mini_\"] [data-testid=\"stMetricLabel\"]{padding:0 0.65rem!important;}"
        "[class*=\"st-key-mini_\"] [data-testid=\"stMetricLabel\"] *"
        "{font-size:0.66rem!important;white-space:nowrap!important;}"
        # same font-shrink for the custom metric cards (the mini trio) so they fit the row
        "[class*=\"st-key-mini_\"] .wxcard{padding:0.5rem 0.4rem 0.55rem!important;}"
        "[class*=\"st-key-mini_\"] .wxcard-v{font-size:1.1rem!important;}"
        "[class*=\"st-key-mini_\"] .wxcard-l{font-size:0.66rem!important;white-space:nowrap!important;}"
        # On phones these metrics sit in multi-column grids, where a card-relative tooltip
        # runs off the left/right screen edge. Pin it to a fixed full-width bottom sheet
        # (8px insets) so it always stays fully on-screen, whichever box you tap.
        ".st-key-top_metrics .wxqt,[class*=\"st-key-mini_\"] .wxqt"
        "{position:fixed!important;left:8px!important;right:8px!important;bottom:auto!important;"
        "top:6.4rem!important;width:auto!important;max-width:none!important;}"
        # keep the page title on one line on phones
        ".stApp h1{font-size:1.7rem!important;}"
        # pin the switcher to the viewport top. position:sticky doesn't hold inside
        # Streamlit's nested block DOM (it just scrolls away), so use position:fixed
        # — sat just below Streamlit's opaque header (~2.5rem, the app's existing top
        # trim) so the header's ⋮ menu stays reachable. Content is pushed down (main
        # container padding-top) to clear the fixed bar.
        ".wx-toggle-bar{display:flex!important;position:fixed;top:3rem;left:0;"
        "right:0;z-index:1000000;}"
        # hide the sticky High/Low bar while the sidebar menu is open, so it doesn't sit
        # on top of the menu (the bar's z-index is above the sidebar overlay).
        ".stApp:has([data-testid=\"stSidebar\"][aria-expanded=\"true\"]) .wx-toggle-bar"
        "{display:none!important;}"
        ".st-key-wx_toggle_wrap{display:block!important;margin:0;}"
        "[data-testid=\"stMainBlockContainer\"]{padding-top:6rem!important;}"
        # pre-JS default: show High until the bridge sets an explicit body class
        "body:not(.wx-show-high):not(.wx-show-low) "
        "[data-testid=\"stColumn\"]:has(.st-key-wx_sec_low){display:none!important;}"
        # explicit selection from the JS bridge (hash or featured default)
        "body.wx-show-high [data-testid=\"stColumn\"]:has(.st-key-wx_sec_low)"
        "{display:none!important;}"
        "body.wx-show-low [data-testid=\"stColumn\"]:has(.st-key-wx_sec_high)"
        "{display:none!important;}"
        "}\n"
        "[data-testid=\"stCaptionContainer\"],[data-testid=\"stCaptionContainer\"] p"
        "{color:var(--muted)!important;}\n"
        # themed, center-justified HTML tables (Streamlit's canvas dataframe can't center)
        ".wtbl-wrap{background:var(--surface);border:1px solid var(--border);border-radius:10px;"
        "overflow-x:auto;overflow-y:hidden;margin:0.3rem 0 0.4rem;scrollbar-width:none;}\n"
        # hide the horizontal scrollbar (the strip under wide tables); trackpad still scrolls
        ".wtbl-wrap::-webkit-scrollbar{display:none;}\n"
        "table.wtbl{width:100%;border-collapse:collapse;font-family:'Bitter',serif;"
        "font-size:0.86rem;font-variant-numeric:tabular-nums;margin:0!important;}\n"
        # keep the contract label (e.g. '99 to 100') on a single line
        "table.wtbl td:first-child,table.wtbl th:first-child{white-space:nowrap;}\n"
        "table.wtbl thead th{font-weight:700;color:var(--accent-strong);background:var(--surface2);"
        "text-align:center;padding:0.5rem 0.6rem;border-bottom:1.5px solid var(--border);"
        "white-space:nowrap;}\n"
        "table.wtbl td{text-align:center;padding:0.42rem 0.6rem;border-bottom:1px solid var(--border);"
        "color:var(--ink);white-space:nowrap;}\n"
        "table.wtbl td.hold{background:rgba(229,120,110,0.22);color:var(--bad);font-weight:700;}\n"
        "table.wtbl td.buy{color:var(--good);font-weight:700;}\n"
        # Streamlit's own chrome doesn't read our palette — repaint it to match
        "[data-testid=\"stHeader\"]{background:var(--bg)!important;}\n"
        "[data-testid=\"stToolbar\"]{background:transparent!important;}\n"
        # the on-hover fullscreen/show-data/download toolbar over charts: strip the
        # slate box (background + border on the toolbar AND every inner wrapper),
        # then paint only the buttons brown.
        "[data-testid=\"stElementToolbar\"],[data-testid=\"stElementToolbar\"] *"
        "{background-color:transparent!important;border-color:transparent!important;"
        "box-shadow:none!important;}\n"
        "[data-testid=\"stElementToolbar\"] button,"
        "[data-testid=\"stElementToolbarButton\"]{background-color:var(--surface)!important;"
        "color:var(--ink)!important;}\n"
        "[data-testid=\"stElementToolbar\"] button:hover,"
        "[data-testid=\"stElementToolbarButton\"]:hover"
        "{background-color:var(--surface2)!important;}\n"
        # the hover-text popup on those buttons (base-web tooltip) — was slate
        "[data-baseweb=\"tooltip\"],[data-baseweb=\"tooltip\"] div,[role=\"tooltip\"]"
        "{background:var(--surface)!important;color:var(--ink)!important;}\n"
        # the fullscreen backdrop + its slate frame border, shown when expanded
        "[data-testid=\"stFullScreenFrame\"],[data-testid=\"stFullScreenFrame\"] > div"
        "{background:var(--bg)!important;}\n"
        "[data-testid=\"stFullScreenFrame\"] *{border-color:var(--border)!important;}\n"
        # the Vega tooltip box that appears when hovering the consensus line
        "#vg-tooltip-element,#vg-tooltip-element.vg-tooltip{background:var(--surface)!important;"
        "border-color:var(--border)!important;color:var(--ink)!important;}\n"
        "#vg-tooltip-element td,#vg-tooltip-element th{color:var(--ink)!important;}\n"
        # themed bordered container ('box around each section')
        "[data-testid=\"stVerticalBlockBorderWrapper\"]{background:var(--surface)!important;"
        "border:1px solid var(--border)!important;border-radius:12px!important;}\n"
        # the sidebar 'Settings' expander panel (was slate when opened)
        "[data-testid=\"stExpander\"] details,[data-testid=\"stExpander\"] summary,"
        "[data-testid=\"stExpanderDetails\"]{background:var(--surface)!important;"
        "border-color:var(--border)!important;}\n"
        # bordered 'Safest hold' section with three equal, centered, single-line boxes
        ".wbox{background:var(--surface);border:1px solid var(--border);border-radius:12px;"
        "padding:0.9rem 1rem;margin:0.4rem 0 0.3rem;}\n"
        ".wboxtitle{font-family:'Bitter',serif;font-weight:700;font-size:0.95rem;"
        "color:var(--ink);margin:0 0 0.6rem;}\n"
        ".wbox .wnote{color:var(--muted);font-size:0.85rem;margin:0;}\n"
        ".wmini3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0.7rem;}\n"
        ".wmini{background:var(--surface2);border:1px solid var(--border);border-radius:10px;"
        "padding:0.6rem 0.35rem;text-align:center;min-width:0;}\n"
        ".wmini .wlabel{font-size:0.72rem;font-weight:700;color:var(--muted);white-space:nowrap;}\n"
        ".wmini .wval{font-size:0.9rem;font-weight:700;color:var(--ink);white-space:nowrap;"
        "margin-top:0.15rem;}\n"
        # mobile High/Low floating switcher — hidden on desktop; the ≤640px block
        # below flips it to flex. Styled from the palette vars so it follows the
        # active theme. Buttons carry data-wx-sel; the JS bridge wires the taps.
        ".wx-toggle-bar{display:none;gap:0.5rem;padding:0.5rem 0.7rem;"
        "background:var(--surface);border-bottom:1px solid var(--border);"
        "box-shadow:0 4px 14px rgba(0,0,0,0.28);}\n"
        # the sticky wrapper is an empty container on desktop — hide it so it adds
        # no vertical gap (the media query flips it to a sticky block on mobile)
        ".st-key-wx_toggle_wrap{display:none;}\n"
        ".wx-toggle-btn{flex:1 1 50%;text-align:center;cursor:pointer;user-select:none;"
        "font-family:'Bitter',serif;font-weight:700;font-size:0.9rem;color:var(--muted);"
        "background:var(--surface2);border:1px solid var(--border);border-radius:10px;"
        "padding:0.6rem 0.4rem;white-space:nowrap;}\n"
        ".wx-toggle-btn b{color:var(--ink);}\n"
        ".wx-toggle-btn.wx-active{color:var(--bg);background:var(--accent);"
        "border-color:var(--accent-strong);}\n"
        ".wx-toggle-btn.wx-active b{color:var(--bg);}\n"
        # keep the zero-height JS-bridge component from adding vertical space
        ".st-key-wx_bridge,.st-key-wx_bridge iframe{height:0!important;min-height:0!important;"
        "margin:0!important;border:0!important;}\n"
        # Custom metric card + hover/tap tooltip. Streamlit's native help= tooltip is
        # hover-only (needs a long-press on touch) and its box runs off the right edge on
        # phones; this bubble opens on hover OR a single tap (focusable), and its panel is
        # width-capped + right-anchored so it wraps instead of clipping.
        ".wxcard{background:var(--surface);border:1px solid var(--border);border-radius:12px;"
        "padding:0.7rem 0.9rem 0.8rem;text-align:center;position:relative;margin-bottom:0.65rem;}\n"
        ".wxcard-l{font-weight:700;color:var(--muted);font-size:0.76rem;margin-bottom:0.1rem;}\n"
        ".wxcard-v{font-size:1.55rem;color:var(--ink);white-space:nowrap;}\n"
        ".wxq{position:absolute;top:5px;right:7px;width:16px;height:16px;line-height:15px;"
        "border-radius:50%;background:var(--surface2);border:1px solid var(--border);"
        "color:var(--muted);font-size:11px;font-weight:700;text-align:center;cursor:pointer;"
        "user-select:none;outline:none;z-index:2;}\n"
        # tooltip positioned relative to the CARD (default: below the ?, right-anchored so
        # it extends left within a full-width card). On the phone multi-column grids it's
        # switched to a fixed full-width bottom sheet (below) so it can never clip an edge.
        ".wxqt{position:absolute;top:28px;right:6px;z-index:1000;width:max-content;"
        "max-width:min(240px,74vw);white-space:normal;text-align:left;background:var(--surface);"
        "color:var(--ink);border:1px solid var(--border);border-radius:8px;padding:0.55rem 0.7rem;"
        "font-size:0.72rem;font-weight:500;line-height:1.35;box-shadow:0 6px 18px rgba(0,0,0,0.28);"
        "opacity:0;visibility:hidden;transition:opacity 0.12s;pointer-events:none;}\n"
        ".wxq:hover ~ .wxqt,.wxq:focus ~ .wxqt,.wxcard:focus-within .wxqt"
        "{opacity:1;visibility:visible;}\n"
        # desktop: the leftmost top-metrics card (Current Temp / Balance) would extend its
        # tooltip left into the sidebar/screen edge — anchor that one to extend right.
        ".st-key-top_metrics [data-testid=\"stColumn\"]:first-child .wxqt"
        "{left:6px;right:auto;}\n"
        "</style>",
        unsafe_allow_html=True,
    )


def metric_card(label, value, help_text=None):
    """A metric box as custom HTML (matches the stMetric look) with an optional info
    bubble that opens on hover OR a single tap and never clips off the right edge —
    Streamlit's native `help=` tooltip can't do tap-to-open on touch. Render with
    `col.markdown(metric_card(...), unsafe_allow_html=True)`."""
    import html as _h
    q = ""
    if help_text:
        # tooltip is a SIBLING of the ? (not nested) so it can be positioned relative to
        # the card, letting the anchor flip per screen-position so it never clips.
        q = (f'<span class="wxq" tabindex="0" role="button" aria-label="{_h.escape(str(label))} info">?</span>'
             f'<span class="wxqt">{_h.escape(str(help_text))}</span>')
    return (f'<div class="wxcard">{q}'
            f'<div class="wxcard-l">{_h.escape(str(label))}</div>'
            f'<div class="wxcard-v">{_h.escape(str(value))}</div></div>')


def _chart_colors():
    """Chart hues for the active palette. Deep slate keeps the bright default
    red/blue/green; Charcoal softens them to terracotta (high), blue-grey (low),
    and puts the Kalshi line on the table-header accent."""
    if st.session_state.get("wx_theme") == "Charcoal":
        return {"high": "#C97B5E", "low": "#8794A6",
                "kalshi": THEMES["Charcoal"]["accent_strong"], "temp": "#B7A99A"}
    return {"high": "#ff6b6b", "low": "#4dabf7", "kalshi": "#51cf66", "temp": "#adb5bd"}


def _seed_theme():
    """Resolve the active palette, seeding session state from the URL query param
    on a fresh load so a chosen theme survives a browser refresh."""
    if "wx_theme" not in st.session_state:
        qp = st.query_params.get("theme")
        st.session_state["wx_theme"] = qp if qp in THEMES else DEFAULT_THEME
    return st.session_state["wx_theme"]


def _theme_controls():
    """Palette picker — lives in the left sidebar with the Day/Safe-hold controls,
    inside a collapsible 'Settings' section. Persists the choice to the URL so it
    becomes the default on the next load, then injects it."""
    _seed_theme()
    with st.sidebar.expander("Settings", expanded=False):
        st.radio("Theme", list(THEMES), key="wx_theme")
    if st.query_params.get("theme") != st.session_state["wx_theme"]:
        st.query_params["theme"] = st.session_state["wx_theme"]
    _inject_theme(st.session_state["wx_theme"])


def _fmt_clock(iso, with_seconds=False):
    """ISO timestamp -> 12-hour clock string (e.g. '2:47:36 PM')."""
    try:
        fmt = "%-I:%M:%S %p" if with_seconds else "%-I:%M %p"
        return datetime.fromisoformat(iso).strftime(fmt)
    except (ValueError, TypeError):
        return iso


def _html_table(df, buy_cols=(), hold_col=None, hold_val=None, container=None):
    """Render `df` as a themed, center-justified HTML table (see .wtbl CSS).

    All cell values must already be display strings. `buy_cols` are tinted green
    when non-empty; when a row's `hold_col` equals `hold_val`, its first cell is
    tinted red — the 'too wide-spread to flip, hold to settlement' cue. Renders
    into `container` (e.g. a bordered st.container) when given, else the page.
    """
    sink = container or st
    cols = list(df.columns)
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = []
    for _, r in df.iterrows():
        flag = hold_col is not None and str(r.get(hold_col, "")) == hold_val
        cells = []
        for i, c in enumerate(cols):
            classes = []
            if i == 0 and flag:
                classes.append("hold")
            if c in buy_cols and str(r[c]).strip() not in ("", "—"):
                classes.append("buy")
            cls = f' class="{" ".join(classes)}"' if classes else ""
            cells.append(f"<td{cls}>{r[c]}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    sink.markdown(
        '<div class="wtbl-wrap"><table class="wtbl"><thead><tr>' + head
        + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _html_df(df, container=None):
    """Render a DataFrame (with a meaningful index) as a themed HTML table —
    the index becomes the first column, floats are trimmed to a few decimals,
    and column labels are kept as-is. Used for the Model-Accuracy tables so they
    follow the palette (the canvas st.dataframe cannot be recolored via CSS)."""
    d = df.reset_index().astype(object)

    def _fmt(v):
        if isinstance(v, float):
            return f"{v:.3f}".rstrip("0").rstrip(".")
        return str(v)

    for c in d.columns:
        d[c] = d[c].map(_fmt)
    _html_table(d, container=container)


def _reliability_chart(rdf):
    """Reliability line chart on a transparent background so it follows the
    palette (observed line vs the ideal diagonal)."""
    long = rdf.reset_index().melt("predicted", var_name="series",
                                  value_name="value")
    return (alt.Chart(long).mark_line().encode(
                x=alt.X("predicted:Q", title=None),
                y=alt.Y("value:Q", title=None),
                color=alt.Color("series:N",
                                legend=alt.Legend(title=None, orient="top")))
            .properties(height=220, background="transparent")
            .configure_view(fill=None, strokeWidth=0))


@st.cache_data(ttl=30, show_spinner=False)
def _kalshi_implied(day_iso):
    """Kalshi's market-implied expected high/low for `day_iso`, distilled from
    the live contract ladder — shown next to Current temp on both pages. None per
    variable when no priced contracts are live.

    `as_of` is the wall-clock time the ladder was actually fetched (frozen with the
    cache entry, so it reflects the real fetch, not the render). The page shows it
    so a stale market reading can't masquerade as a live disagreement with the
    model — the two halves of the comparison carry their own timestamps."""
    from sources import kalshi
    out = {"as_of": datetime.now(_TZ).strftime("%-I:%M:%S %p")}
    for var in ("high", "low"):
        try:
            f = kalshi.implied_forecast(var, date.fromisoformat(day_iso))
        except Exception:
            f = None
        out[var] = f["ev"] if f else None
    return out


@st.cache_data(ttl=120, show_spinner=False)
def _consensus_history():
    """Cached intraday consensus samples (the whole file; filtered per chart)."""
    import consensus_log
    try:
        return consensus_log.load()
    except Exception:
        return []


def _chart_window(day_iso, variable, is_today):
    """(start, end) naive datetimes the through-the-day chart should span, or None.

    Pinned to the target day's active window for every day, not just today: the
    high forms midday, so we show 8am-10pm of the day and drop the overnight/
    previous-day clutter; the low forms near dawn, so we show midnight through
    11am. On a future day the captures accumulate as pre-day lead-up, so the
    chart stays empty until the window opens (e.g. the tomorrow low starts
    populating at midnight).
    """
    d = date.fromisoformat(day_iso)
    if variable == "high":
        return (datetime.combine(d, time(8, 0)), datetime.combine(d, time(22, 0)))
    return (datetime.combine(d, time(0, 0)),
            datetime.combine(d, time(11, 0)))


def consensus_history_df(rows, day_iso, variable, basis, include_temp,
                         is_today=False):
    """Time-indexed df of consensus (+ live temp / Kalshi line) for one series.

    The series is clipped to the variable's active window (see `_chart_window`)
    so the previous day and dead overnight hours don't waste space. None when
    fewer than two points fall inside the window."""
    window = _chart_window(day_iso, variable, is_today)
    pts = [r for r in rows
           if r.get("target_date") == day_iso and r.get("variable") == variable
           and r.get("basis", "hourly") == basis]
    pts.sort(key=lambda r: r["captured_at"])
    data = []
    for r in pts:
        # Naive local wall-clock time: keeps the x-axis labelled in station-local
        # clock time regardless of the viewer's browser timezone, and Altair only
        # accepts naive/UTC datetimes for explicit axis tick values.
        t = datetime.fromisoformat(r["captured_at"]).replace(tzinfo=None)
        if window and not (window[0] <= t <= window[1]):
            continue
        row = {"time": t, "consensus": r.get("consensus")}
        if include_temp and r.get("current_temp") is not None:
            row["current temp"] = r["current_temp"]
        # The market's implied extreme at this sample (CLI/Kalshi snapshots only),
        # so the chart can carry Kalshi's own forecast line next to the model's.
        if r.get("market_ev") is not None:
            row["kalshi (market)"] = r["market_ev"]
        data.append(row)
    if len(data) < 2:
        return None
    return pd.DataFrame(data).set_index("time")


def consensus_chart(hist, variable, day_iso=None, is_today=False, view_window=None,
                    colors=None):
    """Altair line chart of consensus (and today's live temp) through the day.

    Built by hand (rather than st.line_chart) so we can: label the x-axis with
    clock times (not dates) at 30-minute ticks; pad the y-window to 10°F past the
    lowest/highest point so the curves fill the plot instead of bunching against a
    fixed axis; mark every sample with a visible dot; and show
    one combined, swatch-free readout only while hovering a dot (nothing off it).

    The x-axis is pinned to the variable's active window (see `_chart_window`)
    so it spans the full daytime/overnight span from the start rather than
    stretching to fit whatever has accumulated so far.
    """
    # `view_window` (from the zoom slider) pins the x-axis to a user-chosen span
    # and overrides today's default active-window pinning.
    window = view_window or (_chart_window(day_iso, variable, is_today)
                             if day_iso else None)
    df = hist.reset_index()
    value_cols = [c for c in df.columns if c != "time"]
    colors = colors or {}
    line_color = colors.get("high" if variable == "high" else "low") or (
        "#ff6b6b" if variable == "high" else "#4dabf7")
    others = [c for c in value_cols if c != "consensus"]
    # Distinct hue for the Kalshi market line; the live-temp overlay stays muted gray.
    series_color = {"kalshi (market)": colors.get("kalshi", "#51cf66"),
                    "current temp": colors.get("temp", "#adb5bd")}
    color_scale = alt.Scale(domain=["consensus"] + others,
                            range=[line_color] + [series_color.get(c, "#adb5bd")
                                                  for c in others])

    # Pad the y-window 10°F past the lowest/highest point so the lines fill the
    # plot instead of bunching against a fixed 50–100 axis (which left big dead
    # bands and squashed the curves). Falls back to 50–100 only when empty.
    vals = pd.concat([df[c] for c in value_cols]).dropna()
    lo = float(vals.min()) - 10 if not vals.empty else 50.0
    hi = float(vals.max()) + 10 if not vals.empty else 100.0

    # Explicit half-hour tick positions (Vega chokes on a 30-min `tickCount`
    # interval object). labelOverlap drops labels that would collide once the
    # day's span grows, while keeping the 30-min tick marks themselves. When the
    # chart is windowed (today), span the full fixed window; otherwise fit data.
    if window:
        tick_lo, tick_hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    else:
        t = pd.to_datetime(df["time"])
        tick_lo, tick_hi = t.min().floor("30min"), t.max().ceil("30min")
    ticks = pd.date_range(tick_lo, tick_hi, freq="30min").to_pydatetime().tolist()
    x_scale = alt.Scale(domain=list(window)) if window else alt.Undefined

    # Long form for the marks; merge every series' value back onto each row so a
    # single dot's tooltip can show the whole combined readout (time + both
    # series) rather than just its own value.
    long = df.melt("time", value_vars=value_cols,
                   var_name="series", value_name="degF").dropna()
    long = long.merge(df, on="time", how="left")

    # Pre-rendered readout per timestamp (time + every series value), for the
    # tap-to-pin label below. Built here because Vega can't easily format a
    # multi-field string itself. One series per line (joined with newlines, drawn
    # with lineBreak) so a long combined readout stacks vertically instead of
    # running off the right edge and hiding the last value (e.g. Kalshi's).
    def _readout(row):
        parts = [pd.to_datetime(row["time"]).strftime("%-I:%M %p")]
        parts += [f"{c} {row[c]:.1f}°" for c in value_cols if pd.notna(row[c])]
        return "\n".join(parts)
    labels = df.assign(label=df.apply(_readout, axis=1))

    base = alt.Chart(long).encode(
        x=alt.X("time:T", title=None, scale=x_scale,
                axis=alt.Axis(format="%-I:%M %p", values=ticks,
                              labelOverlap=True, labelAngle=-40)),
        y=alt.Y("degF:Q", title="°F", scale=alt.Scale(domain=[lo, hi])),
        color=alt.Color("series:N", scale=color_scale,
                        legend=alt.Legend(title=None, orient="top")),
    )
    lines = base.mark_line(strokeWidth=2.5, clip=True)

    # Tap/click a dot to pin its readout (mobile-friendly: touch devices don't
    # fire the hover events that drive Vega tooltips, so the hover-only readout
    # never appeared on a tap). The selection keys on the timestamp so any series'
    # dot at that time pins the same combined row.
    pick = alt.selection_point(on="click", nearest=True, fields=["time"],
                               empty=False, clear="dblclick")
    # Visible dot at every sample — an easy hover/tap target. Tooltip still serves
    # desktop hover; the selection drives the pinned label for touch.
    dots = base.mark_point(filled=True, opacity=1, clip=True).encode(
        size=alt.condition(pick, alt.value(140), alt.value(55)),
        tooltip=[alt.Tooltip("time:T", title="time", format="%-I:%M %p")] +
                [alt.Tooltip(f"{c}:Q", title=c, format=".1f")
                 for c in value_cols],
    ).add_params(pick)
    # Pinned readout for the tapped point, anchored top-left so it never clips off
    # the plot edge. One line per series (lineBreak) so the full readout stays in
    # view. Shows only while a dot is selected.
    pinned = alt.Chart(labels).mark_text(
        align="left", baseline="top", x=6, y=4, fontSize=13, fontWeight="bold",
        lineBreak="\n", lineHeight=15, color=line_color,
    ).encode(text="label:N").transform_filter(pick)

    # Zoom is driven by the time-window slider in the caller (which re-pins the
    # x-axis via `view_window`), not by Vega's scale-bound gestures — those are
    # too jittery on touch and fought with tap-to-pin / page scroll.
    return ((lines + dots + pinned)
            .properties(height=220, background="transparent")
            .configure_view(fill=None, strokeWidth=0))


def reliability_df(bins):
    """Reliability bins -> df for st.line_chart: observed vs the ideal diagonal."""
    if not bins:
        return None
    df = pd.DataFrame(bins).set_index("predicted")[["observed"]]
    df["ideal (perfect)"] = df.index
    return df


def cents(x):
    return "—" if x is None else f"{round(x * 100)}¢"


def spread_c(ask, bid):
    """Bid-ask spread (the round-trip cost of flipping), or None if a side is
    missing. Equals how far the bid must climb just to break even on a flip."""
    if ask is None or bid is None:
        return None
    return ask - bid


def exit_plan(ask, bid):
    """The realistic way out of a long bought at `ask`.

    Flipping for +20% needs the *bid* to reach ask*1.2, so it only makes sense
    when the spread is tighter than that 20% target; a wider spread means the
    friction dwarfs the profit and you should hold to settlement, where the
    spread costs nothing (it pays the full 100¢).
    """
    if ask is None:
        return "—"
    target = ask * 1.2
    if target >= 1.0:
        return "hold (caps 100¢)"
    sp = spread_c(ask, bid)
    if sp is None or sp > 0.2 * ask:
        return "hold to settle"
    return f"flip @ {cents(target)}"


def prob_table(probs: dict, variable: str, observed=None, top: int = 14) -> pd.DataFrame:
    """Bins with non-trivial probability, sorted by temperature.

    `chance %` is the cumulative model probability the value lands at this bin
    or beyond, in the direction that settles the contract: High = this degree
    or hotter (P value >= bin), Low = this degree or colder (P value <= bin).
    It runs from ~100% at the near end down to ~0% at the far tail.
    """
    items = [(k, v) for k, v in probs.items() if v >= 0.005]
    items.sort(key=lambda kv: -kv[1])
    keep = {k for k, _ in items[:top]}
    rows = [(k, probs[k]) for k in probs if k in keep]

    def sort_key(label):
        return (0, -1) if label.startswith("<=") else \
               (2, 1e9) if label.startswith(">=") else (1, int(label))
    rows.sort(key=lambda r: sort_key(r[0]))
    df = pd.DataFrame(rows, columns=["bin", "prob"])
    df["prob %"] = (df["prob"] * 100).round(1)
    cumulative = model.prob_at_least if variable == "high" else model.prob_at_most
    df["chance %"] = [round(cumulative(probs, model.bin_temp(b)) * 100, 1)
                      for b in df["bin"]]
    return df.set_index("bin")


def prob_bar_chart(df, variable, color=None):
    """Bar chart of the per-bin probabilities, with the x-axis pinned to the
    numeric bin order that prob_table emits.

    st.bar_chart treats the string bins as a nominal axis, and Vega-Lite sorts
    those lexicographically — so "100" lands before "99" and the triple-digit
    bin jumps to the wrong side, giving the chart a U/jagged shape. Building the
    chart explicitly lets us force sort=<the dataframe's own order>, keeping
    hotter to the right.
    """
    color = color or ("#ff6b6b" if variable == "high" else "#4dabf7")
    data = df.reset_index()
    return (
        alt.Chart(data)
        .mark_bar(color=color)
        .encode(
            x=alt.X("bin:N", sort=list(df.index), title=None),
            y=alt.Y("prob %:Q", title=None),
            tooltip=[alt.Tooltip("bin:N", title="bin"),
                     alt.Tooltip("prob %:Q", title="prob %", format=".1f")],
        )
        .properties(height=240, background="transparent")
        .configure_view(fill=None, strokeWidth=0)
    )


def lock_status(d, variable):
    """Interpret the nowcast lock state into an actionable badge + buy-window note.

    Returns (level, headline, detail). `level` picks the Streamlit box: "success"
    = the extreme is in and σ has collapsed (prime buy window), "info" = still
    developing, "warning" = a colder/hotter reading is still expected later (e.g.
    an evening front that could undercut the morning low). Built from the
    snapshot's `locked_ratio` plus the consensus-vs-observed gap, which is the
    model's own read on whether the day's extreme is still ahead of us.
    """
    lr = d.get("locked_ratio", 1.0)
    resolved = int((1 - lr) * 100)
    obs = d.get("observed_so_far")
    consensus = d["consensus"]
    floor = getattr(model, "_SIGMA_FLOOR", 0.7)

    # No observations yet (Tomorrow, or pre-dawn today): pure, widest forecast.
    if obs is None:
        window = ("late afternoon (≈4–6pm CDT), after the peak"
                  if variable == "high"
                  else "early morning (≈7–9am CDT), after the dawn trough")
        return ("info", "Pure Forecast — Nothing Observed Yet",
                f"Spread is at its widest. The {variable} won't lock until "
                f"{window}; σ floors near {floor:.1f}°F once it does.")

    if variable == "high":
        if d.get("peak_locked"):
            return ("success", "Locked — Peak Has Passed",
                    f"High is in at {obs:.1f}°F — temperature has fallen back from "
                    f"the peak, so it's observationally settled (σ ≈ "
                    f"{d['sigma_used']:.1f}°F). Prime buy window.")
        if consensus > obs + 1.0:
            return ("info", "Open — Peak Not Reached",
                    f"High still climbing toward ~{consensus:.0f}°F (only "
                    f"{obs:.1f}°F observed so far). Wait for the afternoon peak.")
        if resolved >= 85:
            return ("success", "Locked — Peak Has Passed",
                    f"High is in at {obs:.1f}°F and σ has collapsed to "
                    f"{d['sigma_used']:.1f}°F (floor ~{floor:.1f}). Prime buy window.")
        return ("info", "Locking — Near the Peak",
                f"High ≈ {obs:.1f}°F and tightening ({resolved}% resolved). "
                "Close to the prime window.")

    # variable == "low"
    if d.get("peak_locked"):
        return ("success", "Locked — Dawn Trough Is In",
                f"Low is in at {obs:.1f}°F — temperature has climbed back from the "
                f"trough, so it's observationally settled (σ ≈ "
                f"{d['sigma_used']:.1f}°F). Prime buy window.")
    if consensus < obs - 1.0:
        return ("warning", "Front Risk — Colder Reading Expected Later",
                f"Coldest so far is {obs:.1f}°F but the model sees ~{consensus:.0f}°F "
                "later (possible evening front before midnight). The morning low is "
                "NOT safe to treat as settled — wait or size down.")
    if resolved >= 85:
        return ("success", "Locked — Dawn Trough Is In",
                f"Low is in at {obs:.1f}°F with no colder reading expected; σ "
                f"collapsed to {d['sigma_used']:.1f}°F (floor ~{floor:.1f}). Prime buy window.")
    return ("info", "Locking — Past the Dawn Trough",
            f"Low ≈ {obs:.1f}°F ({resolved}% resolved). Watch the evening for a "
            "front before treating it as final.")


def mobile_toggle_bar_html(high_d, low_d):
    """HTML for the mobile-only floating High/Low switcher bar.

    `high_d`/`low_d` are the per-variable snapshot blocks (each a dict with a
    'consensus' key, or None). Each button carries a data-wx-sel attribute the
    JS bridge wires up, and shows the day's live consensus so both numbers are
    visible without tapping. Hidden on desktop via CSS (.wx-toggle-bar default
    display:none); shown only inside the ≤640px media query."""
    def _v(d):
        if d and d.get("consensus") is not None:
            return f"{d['consensus']}°F"
        return "—"
    return (
        '<div class="wx-toggle-bar">'
        f'<div class="wx-toggle-btn" data-wx-sel="high">HIGH <b>{_v(high_d)}</b></div>'
        f'<div class="wx-toggle-btn" data-wx-sel="low">Low <b>{_v(low_d)}</b></div>'
        '</div>'
    )


def mobile_toggle_bridge_js(default):
    """JS bridge (for components.html) that makes the floating bar work.

    Runs inside the component's sandboxed-but-same-origin iframe and reaches the
    parent document (window.parent) — the standard Streamlit client-side hack.
    On tap it stores the choice in the URL hash (#wxhigh/#wxlow, ignored by
    Streamlit) and toggles a wx-show-high/wx-show-low class on <body>, which the
    CSS uses to hide the other column on mobile. With no hash it applies
    `default` (the featured section), so the choice persists across the 60s
    auto-refresh and Today/Tomorrow switches. `default` must be 'high' or 'low'."""
    if default not in ("high", "low"):
        raise ValueError(f"default must be 'high' or 'low', got {default!r}")
    return (
        "<script>\n"
        "(function(){\n"
        f'  var DEFAULT = "{default}";\n'
        "  function apply(pdoc, sel){\n"
        '    pdoc.body.classList.remove("wx-show-high","wx-show-low");\n'
        '    pdoc.body.classList.add(sel === "low" ? "wx-show-low" : "wx-show-high");\n'
        '    var btns = pdoc.querySelectorAll(".wx-toggle-btn");\n'
        "    for (var i=0;i<btns.length;i++){\n"
        '      btns[i].classList.toggle("wx-active",'
        ' btns[i].getAttribute("data-wx-sel")===sel);\n'
        "    }\n"
        "  }\n"
        "  function wire(){\n"
        "    var pdoc, ploc, phist;\n"
        "    try { pdoc = window.parent.document; ploc = window.parent.location;"
        " phist = window.parent.history; }\n"
        "    catch(e){ return true; }\n"
        '    var btns = pdoc.querySelectorAll(".wx-toggle-btn");\n'
        "    if (!btns.length) return false;\n"
        "    for (var i=0;i<btns.length;i++){\n"
        "      (function(btn){\n"
        "        btn.onclick = function(){\n"
        '          var sel = btn.getAttribute("data-wx-sel");\n'
        '          try { phist.replaceState(null, "",'
        ' sel === "low" ? "#wxlow" : "#wxhigh"); } catch(e){}\n'
        "          apply(pdoc, sel);\n"
        "        };\n"
        "      })(btns[i]);\n"
        "    }\n"
        '    var h = (ploc.hash || "").replace("#","");\n'
        '    apply(pdoc, h === "wxlow" ? "low" : (h === "wxhigh" ? "high" : DEFAULT));\n'
        "    return true;\n"
        "  }\n"
        "  var n = 0, t = setInterval(function(){"
        " if (wire() || ++n > 40) clearInterval(t); }, 50);\n"
        "})();\n"
        "</script>"
    )


@st.cache_data(ttl=60, show_spinner=False)
def _open_positions():
    """The user's OPEN Kalshi positions (Dallas temp), marked to the live bid — for
    the per-market 'Your Open Contracts' section. Empty on missing creds or any
    error, so the section simply hides. Read-only (authenticated portfolio feed)."""
    try:
        import bet_history
        from sources import kalshi_portfolio
        fills = kalshi_portfolio.fills(bet_history.BETS_START)
        settlements = kalshi_portfolio.settlements(bet_history.BETS_START)
        meta = {t: kalshi_portfolio.market_meta(t) for t in {f["ticker"] for f in fills}}
        out = []
        for r in bet_history.build_rows(fills, settlements, meta):
            if r["status"] != "open":
                continue
            out.append({**r,
                        "current_value": kalshi_portfolio.market_price(r["ticker"], r["side"]),
                        "event_date": bet_history._ticker_date(r["ticker"])})
        return out
    except Exception:
        return []


def render_variable(col, title, d, variable, day_iso, adapter, featured=False,
                    safe_min=None, today_iso=None):
    if safe_min is None:
        safe_min = adapter.safe_hold_default
    with col:
        head = f"<h3>{title}"
        if featured:
            head += (
                " <span style='font-family:Bitter,serif;font-size:0.62rem;"
                "font-weight:700;letter-spacing:0.1em;text-transform:uppercase;"
                "color:var(--accent-strong);border:1px solid var(--accent);"
                "border-radius:999px;padding:0.1rem 0.5rem;vertical-align:middle;'>"
                "Featured</span>")
        head += "</h3>"
        st.markdown(head, unsafe_allow_html=True)
        if d is None:
            st.warning("No data.")
            return
        # keyed so a mobile CSS rule keeps these three on one row (not stacked)
        with st.container(key=f"mini_{variable}"):
            c1, c2, c3 = st.columns(3)
        c1.markdown(metric_card("Consensus", f"{d['consensus']}°F"), unsafe_allow_html=True)
        c2.markdown(metric_card("Spread", f"{d['sigma_used']}°F (±1σ)",
                    "One standard deviation of the model's forecast — its error "
                    "bars. About 68% of outcomes should land within ±this of the "
                    "consensus, ~95% within ±2σ. Wider = more uncertain; this is "
                    "what turns the consensus into contract probabilities. It gets "
                    "inflated for day-ahead forecasts until the scoring log matures."),
                    unsafe_allow_html=True)
        locked_pct = int(d.get("resolved", 1 - d["locked_ratio"]) * 100)
        c3.markdown(metric_card("Resolved", f"{locked_pct}%",
                    "How much of the day's uncertainty is already settled by "
                    "observations. 100% ≈ the extreme has happened."),
                    unsafe_allow_html=True)
        if d["observed_so_far"] is not None:
            obs_line = f"Observed so far: {d['observed_so_far']:.1f}°F (hourly, hard bound)"
            # Kalshi settles on the continuous (sub-hourly) CLI extreme, which can
            # run a touch hotter/colder than the routine hourly reading. Show it
            # alongside on the Kalshi page so the caption matches Kalshi's screen.
            cont = d.get("observed_continuous_display", d.get("observed_continuous"))
            if adapter.basis == "cli" and cont is not None:
                obs_line += f"  ·  {cont:.1f}°F (continuous, Kalshi basis)"
            st.caption(obs_line)
        if d.get("cooling_applied"):
            st.caption("Clear/calm night — extra radiational-cooling offset "
                       "applied to the low.")
        from convective import risk_label
        _conv = risk_label(d)
        if _conv:
            st.caption(_conv)

        level, headline, detail = lock_status(d, variable)
        getattr(st, level)(f"**{headline}** — {detail}")

        # Consensus through the day: how the model's consensus has drifted (one
        # point per ~15 min), with today's live temperature overlaid so you can
        # watch the reading climb/fall toward the predicted peak/trough.
        cbox = st.container(border=True)
        cbox.markdown("**Consensus Through the Day**")
        is_today = (day_iso == today_iso)
        hist = consensus_history_df(_consensus_history(), day_iso, variable,
                                    adapter.basis, include_temp=is_today,
                                    is_today=is_today)
        if hist is not None:
            # Time-window zoom: a range slider beats Vega's touch gestures, which
            # glitch on mobile. The picked span re-pins the x-axis (view_window)
            # and the chart re-pads its y-axis to just the visible points, so the
            # lines fill the plot when you zoom in.
            times = hist.index.to_pydatetime().tolist()
            view_window, hist_view = None, hist
            if len(times) > 2 and times[-1] > times[0]:
                start, end = cbox.slider(
                    "Zoom (time window)", min_value=times[0], max_value=times[-1],
                    value=(times[0], times[-1]), step=timedelta(minutes=15),
                    format="h:mm A", label_visibility="collapsed",
                    key=f"zoom_{variable}_{day_iso}")
                sub = hist.loc[start:end]
                if len(sub) >= 2:                 # ignore a too-narrow pick
                    view_window, hist_view = (start, end), sub
            cbox.altair_chart(
                consensus_chart(hist_view, variable, day_iso, is_today, view_window,
                                colors=_chart_colors()),
                use_container_width=True)
            extras = []
            if "current temp" in hist.columns:
                extras.append("the live temperature (watch it converge on the "
                              "predicted peak/trough)")
            if "kalshi (market)" in hist.columns:
                extras.append("Kalshi's market-implied forecast")
            cbox.caption("Model consensus (°F) sampled every ~15 min" +
                         (", with " + " and ".join(extras) + " overlaid."
                          if extras else "."))
        else:
            cbox.caption("Consensus history builds through the day — a point every "
                         "~15 minutes. Check back as it accumulates.")

        probs = d["probabilities"]
        df = prob_table(probs, variable)
        dbox = st.container(border=True)
        dbox.markdown("**Probability Distribution**")
        _cc = _chart_colors()
        dbox.altair_chart(
            prob_bar_chart(df, variable, color=_cc["high" if variable == "high" else "low"]),
            use_container_width=True)
        disp = df.reset_index()[["bin", "prob %", "chance %"]]
        disp.columns = ["Bin", "Prob %", "Chance %"]
        disp["Prob %"] = disp["Prob %"].map(lambda v: f"{v:g}%")
        disp["Chance %"] = disp["Chance %"].map(lambda v: f"{v:g}%")
        _html_table(disp, container=dbox)
        chance_dir = "this degree or hotter" if variable == "high" else "this degree or colder"
        dbox.caption(f"prob % = chance the {variable} lands exactly in that bin. "
                     f"chance % = cumulative chance it's {chance_dir}.")

        # Live market vs the model (contracts + price→model mapping from the adapter).
        mbox = st.container(border=True)
        mbox.markdown(adapter.heading(variable))
        if adapter.basis_note:
            mbox.caption(adapter.basis_note)
        contracts = adapter.fetch(variable, day_iso)
        if not contracts:
            mbox.caption(adapter.no_market_msg)
            return
        rows = []
        picks = []  # actionable buys, for the Top-3 section below
        holds = []  # safe hold-to-settlement candidates, for the Safest-hold box
        for c in contracts:
            p = adapter.model_prob(probs, c)
            ya, na = c["yes_ask"], c["no_ask"]
            yb, nb = c["yes_bid"], c["no_bid"]
            edge_yes = (p - ya) if ya is not None else -9
            edge_no = ((1 - p) - na) if na is not None else -9
            # Safe hold-to-$1 candidates: scan BOTH sides (the safe side may not be
            # the edge-signal side), keep only high win-prob, positively-priced
            # bets that don't cost more than 92¢ (above that the upside is too thin
            # to be worth the capital), and score by risk-adjusted return
            # edge / sqrt(p*(1-p)).
            for h_side, h_win, h_ask in (("YES", p, ya), ("NO", 1 - p, na)):
                if h_ask is None or h_win < safe_min or h_ask > SAFE_HOLD_MAX_ASK:
                    continue
                h_edge = h_win - h_ask
                if h_edge <= 0:
                    continue
                vol = (h_win * (1 - h_win)) ** 0.5 or 1e-9
                holds.append((h_edge / vol, c["label"], h_side, h_win, h_ask, h_edge))
            # Spread + exit plan for the recommended side: flip for +20% only when
            # the bid-ask spread is tight enough to reach it, else hold to settle.
            spread = plan = "—"
            if edge_yes >= edge_no and edge_yes > 0.03:
                signal = f"BUY YES +{edge_yes*100:.0f}"
                picks.append((c["label"], "YES", p, ya, edge_yes, yb))
                spread = cents(spread_c(ya, yb))
                plan = exit_plan(ya, yb)
            elif edge_no > 0.03:
                signal = f"BUY NO +{edge_no*100:.0f}"
                picks.append((c["label"], "NO", 1 - p, na, edge_no, nb))
                spread = cents(spread_c(na, nb))
                plan = exit_plan(na, nb)
            else:
                signal = "—"
            rows.append({
                "Contract": c["label"],
                "Model %": f"{p*100:.0f}%",
                "Yes (Bid/Ask)": f"{cents(yb)}/{cents(ya)}",
                "No (Bid/Ask)": f"{cents(nb)}/{cents(na)}",
                "Spread": spread,
                "Last": cents(c["last"]),
                "Signal": signal,
                "Exit Plan": plan,
            })
        _html_table(pd.DataFrame(rows), buy_cols=("Signal",),
                    hold_col="Exit Plan", hold_val="hold to settle", container=mbox)
        mbox.caption("model % = model's YES probability for that contract. "
                     "signal = buy side with >3pp edge vs the ask. "
                     "spread = ask − bid on the signal's side: how far the bid must "
                     "climb just to break even on a flip. "
                     "exit plan = 'flip @ X' when the spread is tight enough to sell "
                     "for +20%, else 'hold to settle' (where the spread costs nothing). "
                     "A contract shown in red is too wide-spread to flip — hold it to "
                     f"settlement. Prices in ¢, live from {adapter.name} (refreshes ~30s).")

        # Your open positions in THIS market (variable + day), marked to the live bid,
        # with the model's current probability for each. Hidden when you hold none here
        # (or no Kalshi creds). Read-only, from the authenticated portfolio feed.
        open_here = [p for p in _open_positions()
                     if p.get("variable") == variable and p.get("event_date") == day_iso]
        if open_here:
            obox = st.container(border=True)
            obox.markdown(f"**Your Open {variable.capitalize()} Contracts**")
            orows = []
            for p in open_here:
                try:
                    yes_p = adapter.model_prob(probs, p)
                    side_p = yes_p if p["side"] == "yes" else 1 - yes_p
                    model_pct = f"{side_p*100:.0f}%"
                except Exception:
                    model_pct = "—"
                cv, en, qy = p.get("current_value"), p.get("entry"), p.get("qty")
                unreal = (qy * (cv - en)) if (cv is not None and en is not None) else None
                orows.append({
                    "Contract": p["label"], "Side": p["side"].upper(),
                    "Qty": f"{qy:.2f}", "Entry": cents(en), "Now": cents(cv),
                    "Model %": model_pct,
                    "Unreal P&L": ("—" if unreal is None else
                                   (f"+${unreal:,.2f}" if unreal >= 0
                                    else f"−${abs(unreal):,.2f}")),
                })
            _html_table(pd.DataFrame(orows), container=obox)
            obox.caption("Your currently-held contracts in this market, marked to the "
                         "live bid. model % = the model's current probability for the "
                         "side you hold; unreal P&L = qty × (now − entry), not yet "
                         "realized.")

        # Top 3 HOLD-TO-SETTLEMENT trades: the model's best value picks to carry to
        # $1. Scored by edge × return-on-cost EV (geometric mean, edge / sqrt(ask)):
        # rewards real mispricing while lifting cheaper contracts, without letting
        # penny longshots dominate. Held to settlement, so the spread is irrelevant.
        # Gated at ≥60% model win-probability so only genuinely confident bets show.
        TOP3_MIN_CONF = 0.60
        tbox = st.container(border=True)
        tbox.markdown(f"**Top 3 {variable.capitalize()} Hold-to-Settlement Trades** — "
                      "Best Value Held to $1")
        scored = []
        for lbl, side, mp, price, edge, bid in picks:
            if mp < TOP3_MIN_CONF:               # confidence gate
                continue
            ask = max(price, 0.01)               # guard div-by-zero on a 0¢ ask
            ev = edge / ask                      # expected return on capital risked
            score = edge / (ask ** 0.5)          # geometric mean of edge and EV
            scored.append((score, lbl, side, mp, ask, edge, ev, bid))
        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            top = [{
                "Contract": lbl,
                "Side": side,
                "Model %": f"{mp*100:.0f}%",
                "Ask": cents(ask),
                "Spread": cents(spread_c(ask, bid)),
                "Edge (pp)": f"+{edge*100:.0f}",
                "EV %/Cost": f"+{ev*100:.0f}%",
                "Exit": exit_plan(ask, bid),
            } for _, lbl, side, mp, ask, edge, ev, bid in scored[:3]]
            _html_table(pd.DataFrame(top), buy_cols=("Edge (pp)",),
                        hold_col="Exit", hold_val="hold to settle", container=tbox)
            tbox.caption("The model's most likely winning bets for the "
                         f"{variable}, ranked by a blend of edge and expected value "
                         "(this is hold-to-settlement value, so the spread does NOT "
                         "affect the ranking — at settlement it costs nothing). "
                         "edge (pp) = model prob for that side minus the ask. "
                         "EV %/cost = expected return per dollar risked (edge ÷ ask). "
                         "spread / exit = liquidity cue if you change your mind: a wide "
                         "spread (red) means flipping early isn't viable. Only contracts "
                         "clearing both the 3pp edge threshold and "
                         f"{TOP3_MIN_CONF*100:.0f}% model confidence are shown.")
        else:
            tbox.caption(f"No contract clears both the 3pp edge and "
                         f"{TOP3_MIN_CONF*100:.0f}% model-confidence bar right now — "
                         "no high-confidence value buy.")

        # Safest hold-to-$1 pick: the highest risk-adjusted-return bet among the
        # high-confidence, positively-priced contracts. Held to settlement, so the
        # spread is irrelevant — this is the low-variance counterweight to the
        # longshot-friendly Top-3 above.
        safest_title = (f"Safest {variable.capitalize()} Hold to $1 — "
                        "Lowest-Risk Bet to Hold to Settlement")
        if holds:
            holds.sort(key=lambda x: x[0], reverse=True)
            _, lbl, side, win, ask, h_edge = holds[0]
            ev_cost = h_edge / ask          # expected return per dollar risked
            win_ret = (1 - ask) / ask       # return if it settles to $1
            minis = [
                ("Contract", lbl),
                ("Side · Win %", f"{side} · {win*100:.0f}%"),
                ("Ask · Edge", f"{cents(ask)} · +{h_edge*100:.0f}"),
            ]
            cells = "".join(
                f'<div class="wmini"><div class="wlabel">{lab}</div>'
                f'<div class="wval">{val}</div></div>' for lab, val in minis)
            st.markdown(
                f'<div class="wbox"><div class="wboxtitle">{safest_title}</div>'
                f'<div class="wmini3">{cells}</div></div>',
                unsafe_allow_html=True)
            st.caption(
                f"Ranked by risk-adjusted return (edge ÷ outcome volatility), so it "
                f"favors confident, fairly-priced bets over cheap longshots. Hold to "
                f"settlement and the spread costs nothing. If it wins it returns "
                f"**+{win_ret*100:.0f}%** ({cents(ask)}→100¢); expected return is "
                f"**+{ev_cost*100:.0f}%** per dollar after the {(1-win)*100:.0f}% loss "
                f"chance. Must clear {safe_min*100:.0f}% model win-prob and "
                "positive edge.")
        else:
            st.markdown(
                f'<div class="wbox"><div class="wboxtitle">{safest_title}</div>'
                f'<p class="wnote">No contract clears the {safe_min*100:.0f}% '
                "win-probability + positive-edge bar right now — no low-risk hold "
                "available (the market isn't underpricing a safe side).</p></div>",
                unsafe_allow_html=True)


def _render_accuracy(load_accuracy, calib=None):
    """The 'Model Accuracy' expander body — backtest table + reliability charts
    + live self-scoring. `load_accuracy` is the cached () -> (bt, live) callable."""
    bt, live = load_accuracy()
    corr = calibration.active_corrections(calib)
    if corr:
        st.markdown("**Active self-corrections** — adjustments the model has "
                    "learned from its own settled forecasts and is applying now: "
                    + "; ".join(corr) + ".")
    if not bt:
        st.caption("Backtest unavailable (archive fetch failed).")
    else:
        st.markdown("**Backtest** — replays the pipeline over recent settled days. "
                    "Brier/CRPS lower = better; coverage should track its target.")
        mrows = []
        for var, m in bt.items():
            mrows.append({
                "variable": var, "days": m["n_days"],
                "exact bin": f"{m['exact_peak']:.0f}%", "within ±1°F": f"{m['within1']:.0f}%",
                "Brier": m["brier"], "CRPS": m["crps"],
                "MAE °F": m["mae"], "MAE base": m["mae_baseline"],
                "50% cov": f"{m['coverage_50']:.0f}%", "80% cov": f"{m['coverage_80']:.0f}%",
            })
        _html_df(pd.DataFrame(mrows).set_index("variable"))
        st.caption("**exact bin** = how often the model's top (peak) bin is the exact "
                   "settled degree; **within ±1°F** forgives a one-degree miss. These come "
                   "from the deterministic backtest with a flat spread and no same-day "
                   "anchoring, so treat them as a *relative* A/B harness (config vs config "
                   "on the same days), not the live hit rate — see live self-scoring below.")
        st.markdown(
            "**How to read this table** — each row scores the model's high (or low) "
            "predictions over the last *days* settled days:\n"
            "- **Brier** — accuracy of the per-bin probabilities (0 = perfect, lower "
            "is better). Penalizes being both wrong *and* confident.\n"
            "- **CRPS** — like Brier but aware of *how far off* in degrees, so a near "
            "miss is forgiven more than a big one. Lower is better.\n"
            "- **MAE °F** — average error of the single best-guess (consensus) "
            "temperature, in degrees. **MAE base** is the same for a dumb no-bias, "
            "wide-spread baseline; **MAE °F should be lower than MAE base** — that gap "
            "is what the calibration buys you.\n"
            "- **50% cov / 80% cov** — how often the actual temperature landed inside "
            "the model's stated 50% / 80% range. These should sit *near* 50% and 80%. "
            "Much higher = the model is too cautious (ranges too wide); much lower = "
            "overconfident (ranges too tight)."
        )
        st.caption("Rule of thumb: lower Brier/CRPS/MAE = sharper and more accurate; "
                   "coverage near its target = honest uncertainty.")
        rc = st.columns(2)
        for i, var in enumerate(("high", "low")):
            rdf = reliability_df(bt.get(var, {}).get("reliability"))
            if rdf is not None:
                rc[i].caption(f"{var.title()} reliability — predicted vs observed")
                rc[i].altair_chart(_reliability_chart(rdf), use_container_width=True)
        st.caption("**Reliability charts:** x = the probability the model gave, y = how "
                   "often it actually happened. The closer the *observed* line hugs the "
                   "*ideal* diagonal, the better calibrated the model — e.g. things it "
                   "called 30% likely should happen ~30% of the time.")

    if live and live.get("n_settled"):
        st.markdown(f"**Live self-scoring** — {live['n_settled']} settled predictions "
                    "logged from this dashboard (grows daily). This is the *honest* "
                    "exact-bin hit rate: the full live pipeline, graded against settlement.")

        def _pct(v):
            return f"{v:.0f}%" if v is not None else "—"

        lrows = [{"variable": var, "days": m["n"],
                  "exact bin": _pct(m.get("exact_peak")),
                  "within ±1°F": _pct(m.get("within1")), "Brier": m["brier"]}
                 for var, m in live.get("by_variable", {}).items()]
        if lrows:
            _html_df(pd.DataFrame(lrows).set_index("variable"))

        # Per-lead breakout: same-day (anchored) vs day-ahead exact-bin accuracy.
        lead_names = {0: "same-day", 24: "day-ahead", 36: "2-day"}
        leadrows = []
        for bucket, vars_ in sorted(live.get("by_lead", {}).items(), key=lambda kv: int(kv[0])):
            for var, m in vars_.items():
                leadrows.append({
                    "lead": lead_names.get(int(bucket), f"{bucket}h"), "variable": var,
                    "days": m["n"], "exact bin": _pct(m.get("exact_peak")),
                    "within ±1°F": _pct(m.get("within1")),
                })
        if leadrows:
            st.caption("Exact-bin accuracy by lead time — same-day is anchored to live "
                       "observations, so it should beat day-ahead.")
            _html_df(pd.DataFrame(leadrows).set_index(["lead", "variable"]))

        mkt = live.get("market")
        if mkt and mkt.get("n"):
            st.markdown(f"**Market vs model** — {mkt['n']} settled days where the live "
                        "Kalshi price was logged. Point-forecast error (°F) of the "
                        "market's implied temperature vs the model's consensus, against "
                        "CLI settlement. Lower is better.")
            mrows = [{"variable": var, "days": m["n"],
                      "model MAE": m["model_mae"], "market MAE": m["market_mae"],
                      "market closer": f"{m['market_closer_pct']:.0f}%"}
                     for var, m in mkt.get("by_variable", {}).items()]
            if mrows:
                _html_df(pd.DataFrame(mrows).set_index("variable"))
            st.caption("If *market MAE* beats *model MAE* (and 'market closer' > 50%), the "
                       "market is the sharper forecast and deserves weight; if not, the "
                       "model's independence is the edge. Builds as days settle.")
    else:
        st.caption("Live self-scoring will appear here once logged predictions "
                   "start settling (one day's lead).")


def render_page(snap, calib, adapter, load_accuracy):
    """Draw the full dashboard body for one market. `snap`/`calib` come from the
    cached snapshot loader; `adapter` selects the exchange; `load_accuracy` is the
    cached () -> (bt, live) callable for the accuracy expander."""
    st_autorefresh(interval=60_000, key=f"refresh_{adapter.name}")
    _inject_theme(_seed_theme())

    st.title("Dallas Daily High & Low")

    cur = snap.get("current")
    ki = _kalshi_implied(snap["today"]["day"])      # Kalshi market-implied hi/lo (today)
    # keyed container so a mobile-only CSS rule can grid these 6 boxes 2-per-row
    # (instead of Streamlit's one-per-row stack) without touching other columns.
    with st.container(key="top_metrics"):
        top = st.columns(6)
    if cur:
        ch = snap.get("current_hourly")
        _cur_help = f"Live reading as of {_fmt_clock(cur['time'])}."
        if ch and ch.get("time") != cur.get("time"):
            _cur_help += (f" Latest routine hourly (:53 METAR): "
                          f"{ch['temp']}°F at {_fmt_clock(ch['time'])}.")
        top[0].markdown(metric_card("Current Temp", f"{cur['temp']}°F", _cur_help),
                        unsafe_allow_html=True)
    _mkt_as_of = ki.get("as_of")
    _mkt_help = ("Today's market-implied expected {x}, from Kalshi's live contract "
                 "ladder (shown on both pages for reference)."
                 + (f" Ladder fetched {_mkt_as_of}." if _mkt_as_of else ""))
    top[1].markdown(metric_card("Updated", _fmt_clock(snap["updated"], with_seconds=True)),
                    unsafe_allow_html=True)
    top[2].markdown(metric_card("Kalshi High",
                    f"{ki['high']:.1f}°F" if ki.get("high") is not None else "—",
                    _mkt_help.format(x="high")), unsafe_allow_html=True)
    top[3].markdown(metric_card("Kalshi Low",
                    f"{ki['low']:.1f}°F" if ki.get("low") is not None else "—",
                    _mkt_help.format(x="low")), unsafe_allow_html=True)
    if calib:
        top[4].markdown(metric_card("Calib Bias",
                        f"{calib['bias']['deterministic']['high']:+.1f}/"
                        f"{calib['bias']['deterministic']['low']:+.1f}°F",
                        "Shown as high/low. The raw weather models' average signed error over the last "
                        f"{calib.get('n_days', '~45')} settled days, which the model "
                        "subtracts out before forecasting. A −1.0°F high bias means "
                        "the raw models ran ~1°F too hot on highs, so the model pulls "
                        "its high down by that much (and likewise for the low). Near 0 "
                        "= the models are already well-centered."), unsafe_allow_html=True)
        top[5].markdown(metric_card("Day-Ahead σ",
                        f"{calib['sigma']['high']:.1f}/{calib['sigma']['low']:.1f}°F",
                        "Shown as high/low. The model's day-ahead forecast uncertainty — one standard "
                        "deviation (°F), calibrated from how far past forecasts missed. "
                        "Roughly 68% of outcomes land within ±this of consensus, ~95% "
                        "within ±2×. It's the baseline spread for a ~24h-out forecast; "
                        "tomorrow runs wider and today collapses below it as live "
                        "observations lock the extreme in."), unsafe_allow_html=True)

    # Juxtapose the two fetch times so a lagging market reading is visibly stale
    # rather than looking like a live disagreement with the model.
    if _mkt_as_of:
        st.caption(f"Kalshi market as of {_mkt_as_of} · model snapshot "
                   f"{_fmt_clock(snap['updated'], with_seconds=True)} "
                   "(both refresh every ~60s).")

    day = st.sidebar.radio("Day", ["Today", "Tomorrow"], index=0,
                           key=f"day_{adapter.name}")
    st.sidebar.caption("Tomorrow = pure forecast (no observations yet), so wider. "
                       "Best for the early-morning low before bed.")

    safe_pct = st.sidebar.slider(
        "Safe-Hold Risk Floor", min_value=int(adapter.safe_hold_min * 100),
        max_value=95, value=int(adapter.safe_hold_default * 100), step=5,
        format="%d%%", key=f"safe_{adapter.name}",
        help="Minimum model win-probability for the 'Safest hold to $1' box. Higher = "
             "only surface more certain bets (fewer, safer); lower = allow more "
             "candidates (more reward, more risk).")
    safe_min = safe_pct / 100
    st.sidebar.caption(f"Safe-hold box shows the best bet with ≥{safe_pct}% model "
                       "win-probability and positive edge, held to settlement.")

    # 'Settings' palette picker, pinned to the bottom of the sidebar (CSS pushes
    # the sidebar's only expander down via margin-top:auto).
    _theme_controls()

    key = "today" if day == "Today" else "tomorrow"
    pred = snap[key]

    st.subheader(f"{day} — {pred['day']}")
    # Feature the low on Tomorrow (the user's primary before-bed bet).
    feature_low = (key == "tomorrow")
    today_iso = snap["today"]["day"]

    # Mobile-only High/Low switcher, rendered ABOVE the two sections so its wrapper
    # can pin to the top of the viewport as you scroll (desktop hides it and shows
    # both columns). The bar is plain HTML; the JS bridge (zero-height component)
    # wires the taps and persists the choice in the URL hash across the 60s refresh.
    # Default follows the featured section for the day. The wrapper MUST be a keyed
    # st.container: its parent is then the tall main block, so position:sticky can
    # travel down the page — a bare st.markdown wrapper is only as tall as the bar,
    # which would confine the sticky element and stop it from pinning.
    with st.container(key="wx_toggle_wrap"):
        st.markdown(mobile_toggle_bar_html(pred["high"], pred["low"]),
                    unsafe_allow_html=True)
    with st.container(key="wx_bridge"):
        components.html(mobile_toggle_bridge_js("low" if feature_low else "high"),
                        height=0)

    cols = st.columns(2)
    # Keyed wrappers so the mobile CSS can hide the non-selected column via :has().
    with cols[0]:
        high_box = st.container(key="wx_sec_high")
    with cols[1]:
        low_box = st.container(key="wx_sec_low")
    render_variable(high_box, "High", pred["high"], "high", pred["day"], adapter,
                    featured=not feature_low, safe_min=safe_min, today_iso=today_iso)
    render_variable(low_box, "Low", pred["low"], "low", pred["day"], adapter,
                    featured=feature_low, safe_min=safe_min, today_iso=today_iso)

    with st.expander("Per-Source Breakdown"):
        src = snap["sources"][key]
        rows = []
        for group, members in src.items():
            for label, (hi, lo) in sorted(members.items()):
                rows.append({"group": group, "source": label, "high": hi, "low": lo})
        if rows:
            sdf = pd.DataFrame(rows)
            st.caption(f"{len(sdf)} series across {sdf['group'].nunique()} groups "
                       "(ensemble members aggregated into the distribution above).")
            disp = sdf[["source", "group", "high", "low"]].copy()
            disp.columns = ["Source", "Group", "High", "Low"]
            for c in ("High", "Low"):
                disp[c] = disp[c].map(lambda v: "—" if v is None else f"{v:g}")
            _html_table(disp)

    with st.expander("Model Accuracy"):
        if adapter.accuracy_note:
            st.caption(adapter.accuracy_note)
        _render_accuracy(load_accuracy, calib)

    st.caption(adapter.settle_footer)
