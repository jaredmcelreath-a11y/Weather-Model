"""KDFW high/low probability dashboard.

Run with:  streamlit run app.py
Two pages — Kalshi (default) and Robinhood (ForecastEx) — switchable via the sidebar nav.
Each page auto-refreshes; switch Today/Tomorrow and the safe-hold floor in the
sidebar. All rendering lives in market_view; exchange differences in markets.
"""

from __future__ import annotations

import os

import streamlit as st

import accuracy_view
import bet_view
import calibration
import city_view
import config
import edge_view
import forecast_log
import hourly_cities
import hourly_view
import journal_view
import lab_view
import market_view
import status_view
import model
from markets import KALSHI, ROBINHOOD

st.set_page_config(page_title="Texas Daily High & Low", layout="wide")

# On Streamlit Cloud, point the forward log at the GitHub-hosted copy maintained
# by the scheduled Action, so live self-scoring and per-lead sigma persist across
# the platform's ephemeral restarts. Configured via dashboard secrets [github];
# absent locally, where the log is just a file.
try:
    _gh = dict(st.secrets["github"]) if "github" in st.secrets else None
except Exception:
    _gh = None
if _gh:
    os.environ.setdefault("FORECAST_LOG_GH_REPO", _gh.get("repo", ""))
    os.environ.setdefault("FORECAST_LOG_GH_REF", _gh.get("ref", "data"))
    os.environ.setdefault("FORECAST_LOG_GH_TOKEN", _gh.get("token", ""))

# Kalshi read-only API key for the "My Bets" page — seeded from dashboard secrets
# [kalshi] the same way [github] is above; absent locally/on Cloud without the
# secret, where bet_view degrades to an enable-note rather than crashing.
try:
    _kal = dict(st.secrets["kalshi"]) if "kalshi" in st.secrets else None
except Exception:
    _kal = None
if _kal:
    os.environ.setdefault("KALSHI_ACCESS_KEY_ID", _kal.get("access_key_id", ""))
    os.environ.setdefault("KALSHI_PRIVATE_KEY", _kal.get("private_key", ""))

# Open-Meteo API key — routes forecast/ensemble calls through the keyed customer
# endpoint (dedicated quota) so the deployed app's shared egress IP stops getting
# rate-limited (429) on api.open-meteo.com, which was dropping the deterministic
# models from the live consensus. Absent, the free host is used unchanged.
try:
    _om = st.secrets.get("open_meteo") if hasattr(st.secrets, "get") else None
except Exception:
    _om = None
if _om:
    key = _om.get("api_key", "") if hasattr(_om, "get") else str(_om)
    if key:
        os.environ.setdefault("OPEN_METEO_API_KEY", key)

# Autonomous-trader state store — the Trader control page writes params to a
# dedicated GitHub branch [trade] that the trade cron reads. Absent locally/on
# Cloud without the secret, where the Trader page shows an unavailable note.
try:
    _trade = dict(st.secrets["trade"]) if "trade" in st.secrets else None
except Exception:
    _trade = None
if _trade:
    os.environ.setdefault("TRADE_GH_REPO", _trade.get("repo", ""))
    os.environ.setdefault("TRADE_GH_BRANCH", _trade.get("branch", "trade-data"))
    os.environ.setdefault("TRADE_GH_TOKEN", _trade.get("token", ""))

# Multi-city scanner/screen store — the Screen page READS candidates the scan
# Action writes to a dedicated branch [scan]. Without this bridge scan_log's
# transport (which reads os.environ, not st.secrets) sees no repo and the page is
# permanently empty. The repo is public, so `token` is optional for reading —
# supply one anyway to get the authenticated GitHub rate limit (5000/hr) instead
# of the unauthenticated 60/hr shared across Streamlit Cloud's egress IP, which is
# the same shared-IP throttle that already bites Open-Meteo above.
try:
    _scan = dict(st.secrets["scan"]) if "scan" in st.secrets else None
except Exception:
    _scan = None
if _scan:
    os.environ.setdefault("SCAN_GH_REPO", _scan.get("repo", ""))
    os.environ.setdefault("SCAN_GH_BRANCH", _scan.get("branch", "scan-data"))
    os.environ.setdefault("SCAN_GH_TOKEN", _scan.get("token", ""))

# TTL matches the page's 60s autorefresh and the Kalshi market cache (30s) so the
# model snapshot and the market-implied EV are recomputed on the same cycle — a
# 120s model cache next to a 30s market cache let the model lag up to ~2 min behind
# the market, which read on-screen as a (false) model-vs-market disagreement. The
# raw forecast/obs HTTP calls stay cheap: they're backed by the 600s disk cache in
# sources.common, so a tighter st.cache TTL only re-blends, it doesn't refetch.
@st.cache_data(ttl=60, show_spinner="Fetching forecasts and observations…")
def load_snapshot():
    calib = calibration.get(refresh=True)
    return model.snapshot(calib), calib


@st.cache_data(ttl=60, show_spinner="Fetching forecasts and observations…")
def load_snapshot_kalshi(station: str = config.DEFAULT_STATION):
    """Snapshot shifted to the Kalshi/CLI settlement basis via the calibrated
    settlement_offset (absent offset -> behaves like the hourly snapshot).
    Keyed on `station` so Dallas and Austin cache separately."""
    calib = calibration.get(refresh=True, station=station)
    snap = model.snapshot(calib, settle_offset=(calib or {}).get("settlement_offset"),
                          continuous_obs=True, include_candidate=True, station=station)
    return snap, calib


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_accuracy():
    """Backtest (immediate) + live self-scoring (grows as days settle)."""
    import backtest
    import scoring
    bt = live = None
    try:
        bt = backtest.run()
    except Exception:
        pass
    try:
        live = scoring.score()
    except Exception:
        pass
    return bt, live


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_accuracy_kalshi(station: str = config.DEFAULT_STATION):
    """Backtest + live self-scoring on the Kalshi/CLI settlement basis, per station."""
    import backtest
    import scoring
    calib = calibration.get(refresh=True, station=station) or {}
    off = calib.get("settlement_offset")
    bt = live = None
    try:
        # TODO(plan3b): station-aware backtest. The immediate-history backtest is
        # still KDFW; the live self-scoring below already reflects the station.
        bt = backtest.run(cli=True, settle_offset=off)
    except Exception:
        pass
    try:
        live = scoring.score(basis="cli", station=station)
    except Exception:
        pass
    try:
        market = scoring.market_accuracy(station=station)
        if market and market.get("n"):
            live = dict(live or {})
            live["market"] = market
    except Exception:
        pass
    return bt, live


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_recap(station: str = config.DEFAULT_STATION):
    """Yesterday's scorecard for the Morning Recap card (CLI/Kalshi settlement
    basis), including realized bet P&L. Changes at most once a day, so a long TTL
    is fine. None on any error or before yesterday settles."""
    from datetime import date
    import forecast_log
    import recap
    import settlements
    # Realized bet P&L for the scorecard — best-effort (needs the Kalshi portfolio
    # API; absent locally/without the [kalshi] secret, the P&L line just omits).
    bet_rows = None
    try:
        import bet_history
        bet_rows = bet_history.fetch_rows(bet_history.BETS_START)
    except Exception:
        bet_rows = None
    try:
        return recap.yesterday_scorecard(date.today(), settlements.as_map("cli", station=station),
                                          forecast_log.load(station=station), bet_rows=bet_rows)
    except Exception:
        return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_calibration_history(station: str = config.DEFAULT_STATION):
    """Calibration recompute history for the drift sparklines. Changes ~1×/day."""
    import calibration_history
    try:
        # TODO: per-station calibration_history — the recompute log isn't yet
        # namespaced on disk, so every station reads the shared (KDFW) file. The
        # drift sparkline is a secondary visual; live per-station scoring is
        # accurate. Thread `station` here once per-station recompute history lands.
        return calibration_history.load()
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def load_journal(station: str = config.DEFAULT_STATION):
    """Every settled day scored for the Journal page (per station). Changes
    ~daily; 1h TTL keeps same-day bet settlements reasonably fresh. Bet P&L is
    best-effort, account-wide (cloud-only)."""
    from datetime import date
    import settlements
    bet_rows = None
    try:
        import bet_history
        bet_rows = bet_history.fetch_rows(bet_history.BETS_START)   # account-wide
    except Exception:
        bet_rows = None
    return journal_view.assemble(date.today(), settlements.as_map("cli", station=station),
                                 forecast_log.load(station=station), bet_rows)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_lab(station: str = config.DEFAULT_STATION):
    """Scored forward-log experiments for the Lab page (per station). Changes ~daily."""
    import settlements
    rows = forecast_log.load(station=station)
    settled = settlements.as_map("cli", station=station)
    return (lab_view.head_to_head(rows, settled),
            lab_view.per_model_scores(rows, settled))


@st.cache_data(ttl=60, show_spinner=False)
def load_status(station: str = config.DEFAULT_STATION):
    """Plain timestamps/counts for the Status page's checks, per station. Each
    read is best-effort — a missing log yields an 'unknown' card, never a crash."""
    from datetime import date, datetime, timezone
    inputs: dict = {}
    counts: dict = {}

    def _dt(iso):
        # calibration's `computed` stamp is naive; the Action runner writes it
        # in UTC, so read naive stamps as UTC (±5h skew vs a local recompute
        # is immaterial against the 36h amber threshold).
        try:
            d = datetime.fromisoformat(iso)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    try:
        import consensus_log
        rows = consensus_log.load(station=station)
        counts["Consensus History"] = len(rows)
        cli = [r for r in rows if r.get("basis") == "cli"] or rows
        if cli:
            inputs["last_capture"] = _dt(cli[-1].get("captured_at"))
    except Exception:
        pass
    try:
        counts["Forecast Log"] = len(forecast_log.load(station=station))
    except Exception:
        pass
    try:
        import betting_log
        rows = betting_log.load(station=station)
        counts["Betting Log"] = len(rows)
        today = date.today().isoformat()
        inputs["betting_rows_today"] = sum(
            1 for r in rows if r.get("target_date") == today)
    except Exception:
        pass
    try:
        import settlements
        rows = settlements.load(station=station)
        counts["Settlements"] = len(rows)
        days = [date.fromisoformat(r["target_date"]) for r in rows
                if r.get("basis") == "cli" and r.get("target_date")]
        if days:
            inputs["last_settled"] = max(days)
    except Exception:
        pass
    try:
        import calibration_history
        counts["Calibration History"] = len(calibration_history.load())
    except Exception:
        pass
    try:
        calib = calibration.get(refresh=True, station=station) or {}
        inputs["calib_computed"] = _dt(calib.get("computed"))
    except Exception:
        pass
    return inputs, counts


@st.cache_data(ttl=60, show_spinner=False)
def load_portfolio_value():
    """Total Kalshi portfolio worth = cash + open positions marked to market
    (matches the My Bets page's Portfolio figure), the Kelly bankroll default.
    Short TTL so it tracks the live total. None if the portfolio API isn't set up."""
    try:
        import bet_history
        from sources import kalshi_portfolio
        cash = kalshi_portfolio.balance() or 0.0
        rows = bet_history.fetch_rows(bet_history.BETS_START)
        open_mv = 0.0
        for r in rows:
            if r["status"] == "open":
                cv = kalshi_portfolio.market_price(r["ticker"], r["side"])
                if cv is not None:
                    open_mv += r["qty"] * cv
        return cash + open_mv
    except Exception:
        return None


def _page(adapter, snapshot_loader, accuracy_loader, record_basis,
          station=config.DEFAULT_STATION):
    snap, calib = snapshot_loader()
    dropped = snap.get("dropped_sources") or []
    if dropped:
        st.warning(
            "Running on a reduced model set — these feeds were unreachable and "
            "were skipped: " + ", ".join(dropped) + ". The consensus and "
            "probabilities reflect the remaining sources.")
    if record_basis == "cli":
        # Attach the live market's implied forecast so the CLI log can later score
        # market-vs-model (the scheduled Action does the same 24/7).
        try:
            from datetime import date
            from sources import kalshi
            snap["market"] = kalshi.implied_block(
                date.fromisoformat(snap["today"]["day"]),
                date.fromisoformat(snap["tomorrow"]["day"]), station=station)
        except Exception:
            pass
    try:
        forecast_log.record(snap, basis=record_basis)  # auto-routes by snap station
    except Exception:
        pass  # logging must never break the dashboard
    try:
        import consensus_log
        consensus_log.record(snap, basis=record_basis)  # intraday time series
    except Exception:
        pass
    bankroll = load_portfolio_value() if record_basis == "cli" else None
    market_view.render_page(snap, calib, adapter, accuracy_loader,
                             recap_loader=lambda: load_recap(station),
                             history_loader=load_calibration_history,
                             bankroll=bankroll)


def robinhood_page():
    _page(ROBINHOOD, load_snapshot, load_accuracy, "hourly")


def kalshi_page():
    station = city_view.city_control("forecast", arity=2)
    _page(KALSHI, lambda: load_snapshot_kalshi(station),
          lambda: load_accuracy_kalshi(station), "cli", station)


@st.cache_data(ttl=60, show_spinner="Fetching hourly forecast…")
def load_hourly_city(key: str):
    """TWC hourly forecast + nearby PWS current temp for a Hourly-page city.
    60s TTL matches the page autorefresh; the source layer's own TTLs (300s
    hourly, 60s PWS) keep this from refetching every cycle. Only Dallas has a
    configured PWS, so every other city gets None."""
    from sources import wunderground
    c = hourly_cities.city(key)
    pws = wunderground.pws_current(station=c.modeled) if c.modeled else None
    return wunderground.hourly_at(c.lat, c.lon, c.timezone), pws


@st.cache_data(ttl=300, show_spinner=False)
def load_city_cli(key: str):
    """Today's official CLI report for a Hourly-page city, else None.

    Gated to the city's own climate day: probing at midday Central, the newest
    product for the Pacific and Mountain cities is still YESTERDAY's, which
    ungated would label yesterday's high as today's all morning."""
    from datetime import datetime, timezone as _utc
    from sources import nws_cli
    c = hourly_cities.city(key)
    try:
        cli = nws_cli.fetch_latest_for(c.cli_location, ttl=300)
        if cli and cli["report_date"] == hourly_cities.climate_day(
                c, datetime.now(_utc.utc)):
            return cli
    except Exception:
        return None
    return None


def hourly_page():
    # Deliberately NOT city_view: that control is the sticky Dallas/Austin pick
    # shared by every modelled page, and selecting Miami here must not follow the
    # user to Forecast or Journal, which have no data for it.
    key = st.selectbox("City", hourly_cities.keys(), key="hourly_city",
                       format_func=hourly_cities.label,
                       help="Every city Kalshi lists temperature contracts on, "
                            "with the station its market settles on.")
    hourly_view.render(lambda: load_hourly_city(key),
                       cli_report=load_city_cli(key),
                       city=hourly_cities.city(key))


def edge_page():
    market_view._theme_controls()
    st.title("Edge")
    sel, codes = city_view.city_sections("edge", arity=3)
    for code in codes:
        if sel == "Both":
            st.subheader(city_view.display_name(code))
        edge_view.render(station=code)


def journal_page():
    market_view._theme_controls()
    st.title("Journal")
    sel, codes = city_view.city_sections("journal", arity=3)
    for code in codes:
        if sel == "Both":
            st.subheader(city_view.display_name(code))
        journal_view.render(lambda code=code: load_journal(code), station=code)


def lab_page():
    # Live snapshot is best-effort here: the scored tables must render even
    # when the forecast pipeline is down; only the shadow expander needs it.
    market_view._theme_controls()
    st.title("Lab")
    sel, codes = city_view.city_sections("lab", arity=3)
    for code in codes:
        if sel == "Both":
            st.subheader(city_view.display_name(code))
        s = None
        try:
            s, _ = load_snapshot_kalshi(code)
        except Exception:
            s = None
        lab_view.render(lambda code=code: load_lab(code), snap=s, station=code)


def status_page():
    market_view._theme_controls()
    st.title("Status")
    st.caption("Log-derived health for both cities: every check reads the same "
               "data the dashboard already loads — no extra credentials or probes.")
    per, snaps = [], {}
    for code in config.STATION_CODES:
        try:
            snaps[code], _ = load_snapshot_kalshi(code)
        except Exception:
            snaps[code] = None
        inputs, counts = load_status(code)
        per.append((code, inputs, counts))
    status_view.render(per, snaps)


def accuracy_page():
    market_view._theme_controls()
    st.title("Accuracy")
    sel, codes = city_view.city_sections("accuracy", arity=3)
    for code in codes:
        if sel == "Both":
            st.subheader(city_view.display_name(code))
        accuracy_view.render(lambda code=code: load_accuracy_kalshi(code),
                             lambda code=code: load_calibration_history(code), station=code)


def trader_page():
    import trade_view
    trade_view.render()


def screen_page():
    import screen_view
    screen_view.render()


# Robinhood (hourly-basis) page retired from the live site — the model is now
# Kalshi/CLI-only. robinhood_page() and its hourly loaders are kept below,
# unreferenced, so re-listing it here is a one-line revert if ever needed.
st.navigation([
    st.Page(kalshi_page, title="Forecast", default=True),
    st.Page(hourly_page, title="Hourly"),
    st.Page(journal_page, title="Journal"),
    st.Page(bet_view.render, title="History"),
    st.Page(trader_page, title="Trader"),
    st.Page(edge_page, title="Edge"),
    st.Page(lab_page, title="Lab"),
    st.Page(accuracy_page, title="Accuracy"),
    st.Page(status_page, title="Status"),
    st.Page(screen_page, title="Screen"),
]).run()
