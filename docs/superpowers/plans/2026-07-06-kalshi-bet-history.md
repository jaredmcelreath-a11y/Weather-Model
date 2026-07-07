# Kalshi Bet History & Performance Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only "My Bets" page that pulls the user's real Kalshi bets on the Dallas temp markets since 2026-06-22, shows realized P&L and the model's read at bet time, and charts cumulative P&L like a stock chart.

**Architecture:** Four new isolated units — an RSA-signing Kalshi client (`sources/kalshi_auth.py`), normalized portfolio fetchers (`sources/kalshi_portfolio.py`), a pure bet-assembly + reconstruction module (`bet_history.py`), and a page renderer (`bet_view.py`) — wired into `app.py`'s nav. Live-fetch + on-the-fly annotation; no new persistent store. The signed client is read-only (GET only).

**Tech Stack:** Python 3.9, Streamlit, Altair, `requests`, `cryptography` (new, for RSA-PSS), pytest. Interpreter for all commands: `.venv/bin/python` (bare `python3` lacks the project deps).

## Global Constraints

- **Read-only, always.** Only `GET /portfolio/*` and `GET /historical/*` (+ public `GET /markets/*`). No code path places, modifies, or cancels orders.
- **Never expose the private key.** It is read only from env (`KALSHI_ACCESS_KEY_ID`, `KALSHI_PRIVATE_KEY`, seeded from `st.secrets["kalshi"]`), and never appears in any log, print, exception message, or return value.
- **Signing scheme (exact):** sign the ASCII string `f"{ts_ms}{METHOD}{path}"` where `METHOD` is uppercase and `path` includes `/trade-api/v2` and excludes the query string; RSA-PSS, SHA-256, MGF1-SHA-256, salt length = 32 bytes; base64-encode. Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP` (same ms value), `KALSHI-ACCESS-SIGNATURE`.
- **Host / prefix:** `HOST = "https://api.elections.kalshi.com"`, `API_PREFIX = "/trade-api/v2"` (matches the existing `BASE` in `sources/kalshi.py`).
- **Market scope:** Dallas temp series only — `SERIES_PREFIXES = ("KXHIGHTDAL", "KXLOWTDAL")`. `variable_of`: `KXHIGHTDAL*` → `"high"`, `KXLOWTDAL*` → `"low"`.
- **History start:** `BETS_START = date(2026, 6, 22)` — a module constant in `bet_history.py`.
- **Equity curve:** cumulative realized P&L from $0, x = **settlement date**, one step per settled bet.
- **Never crash the dashboard.** Auth/API failures on the page render as `st.warning`/`st.info`, not exceptions (matches the source-outage-resilience pattern).
- **Assumed Kalshi JSON schema** (used by tests; confirm against a real response in Task 5's manual step): fill = `{"trade_id","ticker","side":"yes"|"no","action":"buy"|"sell","count":int,"yes_price":int_cents,"no_price":int_cents,"created_time":ISO8601}`; page wrapper = `{"fills":[...],"cursor":str}` (empty cursor = last page); settlement = `{"ticker","market_result":"yes"|"no","settled_time":ISO8601}` in `{"settlements":[...],"cursor":str}`; public market = `{"market":{"ticker","yes_sub_title","floor_strike","cap_strike","strike_type":"greater"|"less"|"between"}}`.

---

### Task 1: RSA-signing Kalshi client (`sources/kalshi_auth.py`)

**Files:**
- Create: `sources/kalshi_auth.py`
- Test: `tests/test_kalshi_auth.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `KalshiCredentialsError(RuntimeError)`
  - `HOST: str`, `API_PREFIX: str`
  - `load_credentials() -> tuple[str, str]` — `(key_id, private_key_pem)` from env; raises `KalshiCredentialsError` (message names the missing env var, never the value) if either is missing/empty.
  - `auth_headers(method: str, path: str, key_id: str, private_key_pem: str, ts_ms: int | None = None) -> dict` — `path` is the full API path incl `API_PREFIX`; signs `f"{ts_ms}{method.upper()}{path}"`; returns the 3 headers.
  - `signed_get(path: str, params: dict | None = None, timeout: int = 10) -> dict` — `path` is the sub-path after `API_PREFIX` (e.g. `"/portfolio/fills"`); loads creds, GETs `HOST+API_PREFIX+path`, returns parsed JSON.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kalshi_auth.py`:

```python
"""Unit tests for the Kalshi RSA-signing client. No network — signing and header
construction are pure; a throwaway RSA key is generated per test and used to
verify the emitted signature."""

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import sources.kalshi_auth as ka


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return key, pem


def test_auth_headers_signature_verifies_and_has_three_headers():
    key, pem = _keypair()
    ts = 1_700_000_000_000
    path = ka.API_PREFIX + "/portfolio/fills"
    h = ka.auth_headers("get", path, "kid-123", pem, ts_ms=ts)

    assert h["KALSHI-ACCESS-KEY"] == "kid-123"
    assert h["KALSHI-ACCESS-TIMESTAMP"] == str(ts)
    # the signature verifies against the public key over "{ts}GET{path}"
    msg = f"{ts}GET{path}".encode()
    key.public_key().verify(
        base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )  # raises InvalidSignature if wrong — test fails on raise


def test_auth_headers_method_is_uppercased_in_signature():
    key, pem = _keypair()
    ts = 1
    path = ka.API_PREFIX + "/portfolio/settlements"
    h = ka.auth_headers("get", path, "k", pem, ts_ms=ts)
    # signature must verify over the UPPERCASE method, not "get"
    key.public_key().verify(
        base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), f"{ts}GET{path}".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256().digest_size),
        hashes.SHA256())


def test_load_credentials_missing_raises_without_leaking(monkeypatch):
    monkeypatch.delenv("KALSHI_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", "secret-key-material")
    with pytest.raises(ka.KalshiCredentialsError) as e:
        ka.load_credentials()
    assert "KALSHI_ACCESS_KEY_ID" in str(e.value)
    assert "secret-key-material" not in str(e.value)  # never leak the value
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest tests/test_kalshi_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sources.kalshi_auth'` (and `cryptography` must import; if it is missing, `pip install cryptography` into `.venv` — it is added to requirements in Task 5).

- [ ] **Step 3: Implement `sources/kalshi_auth.py`**

```python
"""Authenticated, READ-ONLY Kalshi client — RSA-PSS request signing.

Only ever issues GET requests to /portfolio and /historical endpoints; there is
no order-placing code here by design. Credentials are read from the environment
(seeded from st.secrets["kalshi"] in app.py) and the private key is never logged,
printed, or placed in an exception message.
"""
from __future__ import annotations

import base64
import os
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

HOST = "https://api.elections.kalshi.com"
API_PREFIX = "/trade-api/v2"


class KalshiCredentialsError(RuntimeError):
    """Raised when the Kalshi API key/secret env vars are absent. The message
    names the missing variable but never includes any key material."""


def load_credentials() -> tuple[str, str]:
    key_id = os.environ.get("KALSHI_ACCESS_KEY_ID", "").strip()
    private_key = os.environ.get("KALSHI_PRIVATE_KEY", "").strip()
    if not key_id:
        raise KalshiCredentialsError("KALSHI_ACCESS_KEY_ID is not set")
    if not private_key:
        raise KalshiCredentialsError("KALSHI_PRIVATE_KEY is not set")
    return key_id, private_key


def _sign(private_key_pem: str, message: str) -> str:
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    sig = key.sign(
        message.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode()


def auth_headers(method: str, path: str, key_id: str, private_key_pem: str,
                 ts_ms: int | None = None) -> dict:
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    message = f"{ts_ms}{method.upper()}{path}"
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
        "KALSHI-ACCESS-SIGNATURE": _sign(private_key_pem, message),
    }


def signed_get(path: str, params: dict | None = None, timeout: int = 10) -> dict:
    """GET an authenticated Kalshi endpoint. `path` is the sub-path after the API
    prefix, e.g. "/portfolio/fills". Returns parsed JSON; raises for HTTP errors."""
    key_id, private_key = load_credentials()
    full_path = API_PREFIX + path
    headers = auth_headers("GET", full_path, key_id, private_key)
    resp = requests.get(HOST + full_path, params=params or {}, headers=headers,
                        timeout=timeout)
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest tests/test_kalshi_auth.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd "/Users/jared/Desktop/Weather Model"
git add sources/kalshi_auth.py tests/test_kalshi_auth.py
git commit -m "feat: read-only RSA-signing Kalshi client

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Portfolio fetchers (`sources/kalshi_portfolio.py`)

**Files:**
- Create: `sources/kalshi_portfolio.py`
- Test: `tests/test_kalshi_portfolio.py`

**Interfaces:**
- Consumes (Task 1): `kalshi_auth.signed_get`.
- Produces:
  - `SERIES_PREFIXES: tuple`, `variable_of(ticker: str) -> str | None`
  - `fills(start: date, fetch=None) -> list[dict]` — each: `{"trade_id","ticker","variable","side","action","count","price","ts"}` (`price` in dollars 0–1, `ts` a UTC `datetime`). `fetch` defaults to `kalshi_auth.signed_get`; tests inject a fake.
  - `settlements(start: date, fetch=None) -> dict[str, dict]` — `{ticker: {"result": "yes"|"no", "ts": datetime}}`.
  - `market_meta(ticker: str, fetch_public=None) -> dict` — `{"label","floor","cap","strike_type","variable"}`. `fetch_public` defaults to a public-markets getter; tests inject a fake.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kalshi_portfolio.py`:

```python
"""Unit tests for the Kalshi portfolio fetchers. No network: a fake `fetch` yields
the two-page cursor responses, so pagination, tier-merge, series filtering, date
filtering, dedupe, and price/timestamp normalization are all exercised offline."""

from datetime import date, datetime, timezone

import sources.kalshi_portfolio as kp


def _fake_fills_fetch():
    """Returns a fetch(path, params) that pages /portfolio/fills (2 pages) and
    /historical/fills (1 page, with a duplicate trade_id to test dedupe)."""
    pages = {
        ("/portfolio/fills", None): {"fills": [
            {"trade_id": "t1", "ticker": "KXHIGHTDAL-26JUN22-B97",
             "side": "yes", "action": "buy", "count": 10,
             "yes_price": 42, "no_price": 58, "created_time": "2026-06-22T19:47:00Z"},
        ], "cursor": "c2"},
        ("/portfolio/fills", "c2"): {"fills": [
            {"trade_id": "t2", "ticker": "KXLOWTDAL-26JUN22-B77",
             "side": "no", "action": "buy", "count": 5,
             "yes_price": 30, "no_price": 70, "created_time": "2026-06-22T05:10:00Z"},
            {"trade_id": "t3", "ticker": "KXNOTDALLAS-26JUN22",  # off-series, dropped
             "side": "yes", "action": "buy", "count": 1,
             "yes_price": 50, "no_price": 50, "created_time": "2026-06-22T12:00:00Z"},
            {"trade_id": "t4", "ticker": "KXHIGHTDAL-26JUN20-B95",  # before start, dropped
             "side": "yes", "action": "buy", "count": 2,
             "yes_price": 20, "no_price": 80, "created_time": "2026-06-20T18:00:00Z"},
        ], "cursor": ""},
        ("/historical/fills", None): {"fills": [
            {"trade_id": "t1", "ticker": "KXHIGHTDAL-26JUN22-B97",  # dup of t1, dropped
             "side": "yes", "action": "buy", "count": 10,
             "yes_price": 42, "no_price": 58, "created_time": "2026-06-22T19:47:00Z"},
        ], "cursor": ""},
    }

    def fetch(path, params=None):
        cursor = (params or {}).get("cursor")
        return pages[(path, cursor)]
    return fetch


def test_fills_pages_merges_filters_and_dedupes():
    out = kp.fills(date(2026, 6, 22), fetch=_fake_fills_fetch())
    ids = sorted(f["trade_id"] for f in out)
    assert ids == ["t1", "t2"]                       # off-series/old/dup removed
    t1 = next(f for f in out if f["trade_id"] == "t1")
    assert t1["variable"] == "high"
    assert t1["price"] == 0.42                        # yes buy -> yes_price/100
    assert t1["ts"] == datetime(2026, 6, 22, 19, 47, tzinfo=timezone.utc)
    t2 = next(f for f in out if f["trade_id"] == "t2")
    assert t2["price"] == 0.70                        # no buy -> no_price/100


def test_settlements_keyed_by_ticker():
    def fetch(path, params=None):
        if path == "/portfolio/settlements":
            return {"settlements": [
                {"ticker": "KXHIGHTDAL-26JUN22-B97", "market_result": "yes",
                 "settled_time": "2026-06-23T06:00:00Z"}], "cursor": ""}
        return {"settlements": [], "cursor": ""}
    s = kp.settlements(date(2026, 6, 22), fetch=fetch)
    assert s["KXHIGHTDAL-26JUN22-B97"]["result"] == "yes"
    assert s["KXHIGHTDAL-26JUN22-B97"]["ts"] == datetime(2026, 6, 23, 6, 0, tzinfo=timezone.utc)


def test_market_meta_parses_public_market():
    def fetch_public(ticker):
        return {"market": {"ticker": ticker, "yes_sub_title": "97 to 98",
                           "floor_strike": 97, "cap_strike": 98,
                           "strike_type": "between"}}
    m = kp.market_meta("KXHIGHTDAL-26JUN22-B97", fetch_public=fetch_public)
    assert m == {"label": "97 to 98", "floor": 97, "cap": 98,
                 "strike_type": "between", "variable": "high"}


def test_variable_of():
    assert kp.variable_of("KXHIGHTDAL-26JUN22-B97") == "high"
    assert kp.variable_of("KXLOWTDAL-26JUN22-B77") == "low"
    assert kp.variable_of("KXOTHER-1") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest tests/test_kalshi_portfolio.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sources.kalshi_portfolio'`.

- [ ] **Step 3: Implement `sources/kalshi_portfolio.py`**

```python
"""Normalized, READ-ONLY fetchers over the authenticated Kalshi client.

Pulls the user's fills and settlements for the Dallas temp series (both the recent
/portfolio tier and the older /historical tier), pages through Kalshi's cursor
pagination, filters to the series and start date, and normalizes to plain dicts.
Market metadata (strike range) comes from the PUBLIC markets endpoint (no auth).
"""
from __future__ import annotations

from datetime import date, datetime

from sources import kalshi_auth
from sources.common import get_json

SERIES_PREFIXES = ("KXHIGHTDAL", "KXLOWTDAL")


def variable_of(ticker: str) -> str | None:
    if ticker.startswith("KXHIGHTDAL"):
        return "high"
    if ticker.startswith("KXLOWTDAL"):
        return "low"
    return None


def _parse_ts(s: str) -> datetime:
    # Kalshi timestamps are ISO 8601 with a trailing Z; normalize to +00:00.
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iter_pages(fetch, path, items_key):
    """Yield each item across all cursor pages of `path`."""
    cursor = None
    while True:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        # tests key the fake on (path, cursor) with no cursor -> None
        page = fetch(path, {"cursor": cursor} if cursor else None)
        for item in page.get(items_key) or []:
            yield item
        cursor = page.get("cursor")
        if not cursor:
            return


def fills(start: date, fetch=None) -> list[dict]:
    fetch = fetch or kalshi_auth.signed_get
    seen, out = set(), []
    for path in ("/portfolio/fills", "/historical/fills"):
        for f in _iter_pages(fetch, path, "fills"):
            ticker = f.get("ticker", "")
            var = variable_of(ticker)
            if var is None:
                continue
            ts = _parse_ts(f["created_time"])
            if ts.date() < start:
                continue
            tid = f.get("trade_id")
            if tid in seen:
                continue
            seen.add(tid)
            side = f.get("side")
            price_c = f.get("yes_price") if side == "yes" else f.get("no_price")
            out.append({
                "trade_id": tid, "ticker": ticker, "variable": var,
                "side": side, "action": f.get("action"),
                "count": int(f.get("count", 0)),
                "price": (price_c or 0) / 100.0, "ts": ts,
            })
    return out


def settlements(start: date, fetch=None) -> dict[str, dict]:
    fetch = fetch or kalshi_auth.signed_get
    out: dict[str, dict] = {}
    for path in ("/portfolio/settlements", "/historical/settlements"):
        for s in _iter_pages(fetch, path, "settlements"):
            ticker = s.get("ticker", "")
            if variable_of(ticker) is None:
                continue
            out[ticker] = {"result": s.get("market_result"),
                           "ts": _parse_ts(s["settled_time"])}
    return out


def _public_market(ticker: str) -> dict:
    return get_json(f"{kalshi_auth.HOST}{kalshi_auth.API_PREFIX}/markets/{ticker}",
                    ttl=3600)


def market_meta(ticker: str, fetch_public=None) -> dict:
    fetch_public = fetch_public or _public_market
    m = (fetch_public(ticker) or {}).get("market") or {}
    return {
        "label": m.get("yes_sub_title") or m.get("subtitle") or ticker,
        "floor": m.get("floor_strike"), "cap": m.get("cap_strike"),
        "strike_type": m.get("strike_type"), "variable": variable_of(ticker),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest tests/test_kalshi_portfolio.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd "/Users/jared/Desktop/Weather Model"
git add sources/kalshi_portfolio.py tests/test_kalshi_portfolio.py
git commit -m "feat: normalized Kalshi portfolio fetchers (fills, settlements, meta)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Bet assembly, P&L, summary, equity curve (`bet_history.py`)

**Files:**
- Create: `bet_history.py`
- Test: `tests/test_bet_history.py`

**Interfaces:**
- Consumes (Task 2): the normalized `fills` list, `settlements` dict, and per-ticker `market_meta` dicts.
- Produces:
  - `BETS_START: date` = `date(2026, 6, 22)`
  - `build_rows(fills: list[dict], settlements: dict, meta: dict[str, dict]) -> list[dict]` — one row per ticker (a "bet"), newest first. Row keys: `ticker,label,variable,floor,cap,strike_type,side,entry,qty,first_ts,status,result,settled_ts,pnl`. `status` ∈ `"settled"|"open"`; `pnl` `None` when open.
  - `summary(rows: list[dict]) -> dict` — `n_settled,wins,losses,win_rate,net_pnl,staked,roi,with_model_pct`.
  - `equity_curve(rows: list[dict]) -> list[dict]` — settled rows by `settled_ts`, cumulative: `[{"date": date, "total": float}, ...]`.

**P&L definition (correct for buys/sells/partials):** per ticker, `cash_flow = Σ(sell count×price) − Σ(buy count×price)`; `net_yes = buys_yes−sells_yes`, `net_no = buys_no−sells_no`; `settlement_payout = net_yes` if result `"yes"` else `net_no` (each winning contract pays $1); `pnl = cash_flow + settlement_payout`. `side` = `"yes"` if `net_yes ≥ net_no` else `"no"`; `entry` = avg buy price of that side; `qty` = that side's net count; `staked` = Σ buy cost of that side.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bet_history.py`:

```python
"""Unit tests for bet assembly, P&L, summary, and the equity curve. Pure — inputs
are the already-normalized fills/settlements/meta dicts (no Kalshi, no network)."""

from datetime import date, datetime, timezone

import bet_history as bh


def _fill(tid, ticker, side, action, count, price, day, hour=19):
    return {"trade_id": tid, "ticker": ticker, "variable": "high", "side": side,
            "action": action, "count": count, "price": price,
            "ts": datetime(2026, 6, day, hour, tzinfo=timezone.utc)}


META = {
    "KXHIGHTDAL-26JUN22-B97": {"label": "97 to 98", "floor": 97, "cap": 98,
                               "strike_type": "between", "variable": "high"},
    "KXHIGHTDAL-26JUN23-B99": {"label": "99 to 100", "floor": 99, "cap": 100,
                               "strike_type": "between", "variable": "high"},
}


def test_build_rows_settled_win_pnl_and_fields():
    fills = [_fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22)]
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc)}}
    rows = bh.build_rows(fills, settlements, META)
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "yes" and r["qty"] == 10 and r["entry"] == 0.42
    assert r["status"] == "settled" and r["result"] == "yes"
    # 10 bought @0.42 -> cash_flow -4.20; settled yes -> payout +10; pnl +5.80
    assert round(r["pnl"], 2) == 5.80


def test_build_rows_settled_loss_pnl():
    fills = [_fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22)]
    settlements = {"KXHIGHTDAL-26JUN22-B97":
                   {"result": "no", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc)}}
    r = bh.build_rows(fills, settlements, META)[0]
    assert round(r["pnl"], 2) == -4.20                # lost the stake


def test_open_bet_has_no_pnl_and_is_excluded_from_curve():
    fills = [_fill("t1", "KXHIGHTDAL-26JUN23-B99", "yes", "buy", 4, 0.30, 23)]
    rows = bh.build_rows(fills, {}, META)
    assert rows[0]["status"] == "open" and rows[0]["pnl"] is None
    assert bh.equity_curve(rows) == []


def test_summary_and_curve_across_two_settled_bets():
    fills = [
        _fill("t1", "KXHIGHTDAL-26JUN22-B97", "yes", "buy", 10, 0.42, 22),  # +5.80
        _fill("t2", "KXHIGHTDAL-26JUN23-B99", "yes", "buy", 10, 0.50, 23),  # -5.00 (loss)
    ]
    settlements = {
        "KXHIGHTDAL-26JUN22-B97": {"result": "yes", "ts": datetime(2026, 6, 23, 6, tzinfo=timezone.utc)},
        "KXHIGHTDAL-26JUN23-B99": {"result": "no", "ts": datetime(2026, 6, 24, 6, tzinfo=timezone.utc)},
    }
    rows = bh.build_rows(fills, settlements, META)
    s = bh.summary(rows)
    assert s["n_settled"] == 2 and s["wins"] == 1 and s["losses"] == 1
    assert s["win_rate"] == 50.0
    assert round(s["net_pnl"], 2) == 0.80             # +5.80 - 5.00
    assert round(s["staked"], 2) == 9.20              # 4.20 + 5.00
    curve = bh.equity_curve(rows)
    assert [c["date"] for c in curve] == [date(2026, 6, 23), date(2026, 6, 24)]
    assert round(curve[0]["total"], 2) == 5.80
    assert round(curve[1]["total"], 2) == 0.80        # cumulative
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest tests/test_bet_history.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bet_history'`.

- [ ] **Step 3: Implement `bet_history.py`**

```python
"""Assemble the user's Kalshi fills + settlements into per-market bet rows, with
realized P&L, summary stats, and the cumulative equity curve. Pure functions over
the normalized dicts from sources.kalshi_portfolio — no network, no Streamlit.

Model-at-bet-time annotation lives in the same module (added in Task 4) but is a
separate pass (annotate_rows) so assembly stays model-free.
"""
from __future__ import annotations

from datetime import date

BETS_START = date(2026, 6, 22)


def build_rows(fills: list[dict], settlements: dict, meta: dict) -> list[dict]:
    by_ticker: dict[str, list] = {}
    for f in fills:
        by_ticker.setdefault(f["ticker"], []).append(f)

    rows = []
    for ticker, group in by_ticker.items():
        m = meta.get(ticker, {})
        buys_yes = sum(f["count"] for f in group if f["side"] == "yes" and f["action"] == "buy")
        sells_yes = sum(f["count"] for f in group if f["side"] == "yes" and f["action"] == "sell")
        buys_no = sum(f["count"] for f in group if f["side"] == "no" and f["action"] == "buy")
        sells_no = sum(f["count"] for f in group if f["side"] == "no" and f["action"] == "sell")
        cash_flow = sum((f["count"] * f["price"]) * (1 if f["action"] == "sell" else -1)
                        for f in group)
        net_yes, net_no = buys_yes - sells_yes, buys_no - sells_no
        side = "yes" if net_yes >= net_no else "no"
        qty = net_yes if side == "yes" else net_no
        buy_cost = sum(f["count"] * f["price"] for f in group
                       if f["side"] == side and f["action"] == "buy")
        buy_ct = sum(f["count"] for f in group
                     if f["side"] == side and f["action"] == "buy")
        entry = (buy_cost / buy_ct) if buy_ct else None

        settle = settlements.get(ticker)
        if settle:
            payout = net_yes if settle["result"] == "yes" else net_no
            pnl = cash_flow + payout
            status, result, settled_ts = "settled", settle["result"], settle["ts"]
        else:
            pnl, status, result, settled_ts = None, "open", None, None

        rows.append({
            "ticker": ticker, "label": m.get("label", ticker),
            "variable": m.get("variable"), "floor": m.get("floor"),
            "cap": m.get("cap"), "strike_type": m.get("strike_type"),
            "side": side, "entry": entry, "qty": qty,
            "first_ts": min(f["ts"] for f in group),
            "status": status, "result": result, "settled_ts": settled_ts,
            "pnl": pnl, "staked": buy_cost,
        })
    rows.sort(key=lambda r: r["first_ts"], reverse=True)  # newest first
    return rows


def summary(rows: list[dict]) -> dict:
    settled = [r for r in rows if r["status"] == "settled"]
    wins = sum(1 for r in settled if r["pnl"] > 0)
    losses = sum(1 for r in settled if r["pnl"] <= 0)
    net_pnl = sum(r["pnl"] for r in settled)
    staked = sum(r["staked"] for r in settled)
    annotated = [r for r in settled if r.get("agree") is not None]
    with_model = sum(1 for r in annotated if r["agree"])
    return {
        "n_settled": len(settled), "wins": wins, "losses": losses,
        "win_rate": (100.0 * wins / len(settled)) if settled else 0.0,
        "net_pnl": net_pnl, "staked": staked,
        "roi": (100.0 * net_pnl / staked) if staked else 0.0,
        "with_model_pct": (100.0 * with_model / len(annotated)) if annotated else None,
    }


def equity_curve(rows: list[dict]) -> list[dict]:
    settled = sorted((r for r in rows if r["status"] == "settled"),
                     key=lambda r: r["settled_ts"])
    out, total = [], 0.0
    for r in settled:
        total += r["pnl"]
        out.append({"date": r["settled_ts"].date(), "total": total})
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest tests/test_bet_history.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd "/Users/jared/Desktop/Weather Model"
git add bet_history.py tests/test_bet_history.py
git commit -m "feat: assemble Kalshi bets -> P&L, summary, equity curve

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Model-at-bet-time reconstruction (`bet_history.py`)

**Files:**
- Modify: `bet_history.py` (add reconstruction functions + `annotate_rows`)
- Test: `tests/test_bet_history_annotate.py`

**Interfaces:**
- Consumes: rows from `build_rows`; `betting_log`-style rows (`target_date,variable,captured_at,cli_consensus,sigma_used`), `consensus_history`-style rows (`target_date,variable,basis,captured_at,consensus`), and `calib` (has `calib["sigma"][variable]`).
- Produces:
  - `model_at_bet(fill_ts, variable, floor, cap, strike_type, side, entry, betting_rows, consensus_rows, calib, tol_min=45) -> tuple[float|None, float|None, bool|None]` — `(model_side_prob, edge, agree)`.
  - `annotate_rows(rows, betting_rows, consensus_rows, calib) -> None` — mutates each row, adding `model_prob`, `edge`, `agree` (all `None` when no snapshot within tolerance).

**Reconstruction:** pick the snapshot (betting first, else consensus) whose `captured_at` is nearest `fill_ts` for the same `(target_date=fill_ts.date(), variable)` and within `tol_min`. σ = the betting row's `sigma_used`, else `calib["sigma"][variable]`. `model_yes` from a normal `N(consensus, σ)` with a ±0.5°F continuity correction: `greater` → `1 − Φ(floor−0.5)`; `less` → `Φ(cap+0.5)`; `between` → `Φ(cap+0.5) − Φ(floor−0.5)`. `model_side_prob = model_yes if side=="yes" else 1−model_yes`. `edge = model_side_prob − entry`; `agree = edge > 0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bet_history_annotate.py`:

```python
"""Unit tests for the model-at-bet-time reconstruction: nearest-snapshot pick,
normal-CDF probability over a contract range, edge sign, and the '—' gap."""

from datetime import datetime, timezone

import bet_history as bh


def _bet_row(fill_hour, side="yes", entry=0.42):
    return {"ticker": "KXHIGHTDAL-26JUN22-B97", "variable": "high",
            "floor": 97, "cap": 98, "strike_type": "between", "side": side,
            "entry": entry, "first_ts": datetime(2026, 6, 22, fill_hour, tzinfo=timezone.utc),
            "status": "settled"}


BETTING = [{"target_date": "2026-06-22", "variable": "high",
            "captured_at": "2026-06-22T19:45:00+00:00",
            "cli_consensus": 97.5, "sigma_used": 1.0}]


def test_model_at_bet_uses_nearest_betting_snapshot():
    p, edge, agree = bh.model_at_bet(
        datetime(2026, 6, 22, 19, 47, tzinfo=timezone.utc),
        "high", 97, 98, "between", "yes", 0.42, BETTING, [], calib={})
    # N(97.5, 1.0) over [96.5, 98.5] ~ 0.68; yes side; edge = 0.68 - 0.42 > 0
    assert 0.60 < p < 0.75
    assert edge > 0 and agree is True


def test_no_snapshot_within_tolerance_returns_none():
    p, edge, agree = bh.model_at_bet(
        datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc),  # hours from the 19:45 snap
        "high", 97, 98, "between", "yes", 0.42, BETTING, [], calib={}, tol_min=45)
    assert (p, edge, agree) == (None, None, None)


def test_falls_back_to_consensus_history_with_calib_sigma():
    consensus = [{"target_date": "2026-06-22", "variable": "high", "basis": "cli",
                  "captured_at": "2026-06-22T14:05:00+00:00", "consensus": 99.0}]
    p, edge, agree = bh.model_at_bet(
        datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc),
        "high", 97, 98, "between", "no", 0.55, [], consensus,
        calib={"sigma": {"high": 2.0}})
    # consensus 99 well above [97,98] -> low yes prob -> high NO prob -> positive edge
    assert p is not None and agree is True


def test_annotate_rows_sets_model_fields():
    rows = [_bet_row(19)]
    bh.annotate_rows(rows, BETTING, [], calib={})
    assert rows[0]["model_prob"] is not None
    assert rows[0]["agree"] in (True, False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest tests/test_bet_history_annotate.py -q`
Expected: FAIL — `AttributeError: module 'bet_history' has no attribute 'model_at_bet'`.

- [ ] **Step 3: Add reconstruction to `bet_history.py`**

Add these imports at the top of `bet_history.py` (next to the existing `from datetime import date`):

```python
import math
from datetime import datetime
```

Append to `bet_history.py`:

```python
def _phi(x: float, mu: float, sigma: float) -> float:
    """Normal CDF Φ((x−mu)/sigma) via erf (no scipy dependency)."""
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def _contract_yes_prob(consensus, sigma, floor, cap, strike_type) -> float:
    """Model P(contract settles YES) under N(consensus, sigma), with a ±0.5°F
    continuity correction (temps settle on integers)."""
    if strike_type == "greater":
        return 1.0 - _phi(floor - 0.5, consensus, sigma)
    if strike_type == "less":
        return _phi(cap + 0.5, consensus, sigma)
    return _phi(cap + 0.5, consensus, sigma) - _phi(floor - 0.5, consensus, sigma)


def _nearest(fill_ts, variable, betting_rows, consensus_rows, tol_min):
    """(consensus, sigma_or_None) of the snapshot nearest fill_ts for this
    (date, variable), preferring betting_log (has sigma); None if none within tol."""
    day = fill_ts.date().isoformat()
    best, best_gap = None, tol_min * 60 + 1
    for r in betting_rows:
        if r.get("target_date") != day or r.get("variable") != variable:
            continue
        gap = abs((datetime.fromisoformat(r["captured_at"]) - fill_ts).total_seconds())
        if gap <= tol_min * 60 and gap < best_gap:
            best, best_gap = (r["cli_consensus"], r.get("sigma_used")), gap
    if best is not None:
        return best
    for r in consensus_rows:
        if (r.get("target_date") != day or r.get("variable") != variable
                or r.get("basis") != "cli"):
            continue
        gap = abs((datetime.fromisoformat(r["captured_at"]) - fill_ts).total_seconds())
        if gap <= tol_min * 60 and gap < best_gap:
            best, best_gap = (r["consensus"], None), gap
    return best


def model_at_bet(fill_ts, variable, floor, cap, strike_type, side, entry,
                 betting_rows, consensus_rows, calib, tol_min=45):
    snap = _nearest(fill_ts, variable, betting_rows, consensus_rows, tol_min)
    if snap is None or floor is None and cap is None:
        return (None, None, None)
    consensus, sigma = snap
    if sigma is None:
        sigma = ((calib or {}).get("sigma", {}) or {}).get(variable)
    if not sigma:
        return (None, None, None)
    yes_p = _contract_yes_prob(consensus, sigma, floor, cap, strike_type)
    yes_p = min(max(yes_p, 0.0), 1.0)
    side_p = yes_p if side == "yes" else 1.0 - yes_p
    edge = side_p - entry if entry is not None else None
    return (side_p, edge, (edge > 0) if edge is not None else None)


def annotate_rows(rows, betting_rows, consensus_rows, calib) -> None:
    for r in rows:
        p, edge, agree = model_at_bet(
            r["first_ts"], r["variable"], r["floor"], r["cap"],
            r["strike_type"], r["side"], r["entry"],
            betting_rows, consensus_rows, calib)
        r["model_prob"], r["edge"], r["agree"] = p, edge, agree
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest tests/test_bet_history_annotate.py tests/test_bet_history.py -q`
Expected: PASS (8 passed — the Task 3 tests still pass, plus the 4 new ones).

- [ ] **Step 5: Commit**

```bash
cd "/Users/jared/Desktop/Weather Model"
git add bet_history.py tests/test_bet_history_annotate.py
git commit -m "feat: reconstruct model's read at bet time (normal N(consensus,sigma))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: "My Bets" page + wiring (`bet_view.py`, `app.py`, `requirements.txt`)

**Files:**
- Create: `bet_view.py`
- Modify: `app.py` (seed `KALSHI_*` env from secrets; add the nav page)
- Modify: `requirements.txt` (add `cryptography`)
- Test: `tests/test_bet_view.py` (import + a pure equity-chart helper)

**Interfaces:**
- Consumes: `sources.kalshi_portfolio.{fills,settlements,market_meta}`, `bet_history.{BETS_START,build_rows,summary,equity_curve,annotate_rows}`, `market_view.{_inject_theme,_seed_theme,_html_table,cents,_chart_colors}`, `betting_log.load`, `consensus_log.load`, `calibration.get`.
- Produces: `equity_chart(curve, color) -> alt.Chart`; `render()` (the page entrypoint).

- [ ] **Step 1: Add `cryptography` to requirements**

Edit `requirements.txt` — add a line `cryptography` (any existing pinning style in the file; if others are unpinned, leave unpinned). Then install into the venv:

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pip install cryptography -q && echo installed`
Expected: `installed` (needed for Tasks 1–4 to import; run this before their tests if not already present).

- [ ] **Step 2: Write the failing test**

Create `tests/test_bet_view.py`:

```python
"""Import smoke for the My Bets page + a pure check on the equity chart helper
(the Streamlit render itself needs live credentials, so it's verified manually)."""

from datetime import date


def test_bet_view_imports():
    import bet_view  # must import without side effects / missing names
    assert hasattr(bet_view, "render") and hasattr(bet_view, "equity_chart")


def test_equity_chart_encodes_date_and_total():
    import bet_view
    curve = [{"date": date(2026, 6, 23), "total": 5.8},
             {"date": date(2026, 6, 24), "total": 0.8}]
    spec = bet_view.equity_chart(curve, color="#7FD3A2").to_dict()
    # x is the date field, y is the cumulative total field
    assert spec["encoding"]["x"]["field"] == "date"
    assert spec["encoding"]["y"]["field"] == "total"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -m pytest tests/test_bet_view.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bet_view'`.

- [ ] **Step 4: Implement `bet_view.py`**

```python
"""'My Bets' page — the user's real Kalshi bets on the Dallas temp markets since
BETS_START, with realized P&L, the model's read at bet time, and a cumulative-P&L
equity curve. Read-only. Fetch failures degrade to a warning, never a crash.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import bet_history
import calibration
import consensus_log
import betting_log
import market_view
from sources import kalshi_auth, kalshi_portfolio


@st.cache_data(ttl=60, show_spinner="Loading your Kalshi bets…")
def _load_bets():
    """Fetch + assemble + annotate. Returns (rows, summary, curve). Cached ~60s.
    Raises KalshiCredentialsError when creds are absent (handled by the caller)."""
    fills = kalshi_portfolio.fills(bet_history.BETS_START)
    settlements = kalshi_portfolio.settlements(bet_history.BETS_START)
    meta = {f["ticker"]: kalshi_portfolio.market_meta(f["ticker"])
            for f in fills}
    rows = bet_history.build_rows(fills, settlements, meta)
    bet_history.annotate_rows(rows, betting_log.load(), consensus_log.load(),
                              calibration.get())
    return rows, bet_history.summary(rows), bet_history.equity_curve(rows)


def equity_chart(curve, color):
    """Stock-chart-style line of cumulative P&L (x=date, y=total) on a transparent
    background so it follows the palette, with a zero baseline rule."""
    df = pd.DataFrame(curve)
    line = (alt.Chart(df).mark_line(point=True, strokeWidth=2.5, color=color)
            .encode(x=alt.X("date:T", title=None),
                    y=alt.Y("total:Q", title="Cumulative P&L ($)"),
                    tooltip=[alt.Tooltip("date:T", title="date"),
                             alt.Tooltip("total:Q", title="total", format="$.2f")]))
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        strokeDash=[4, 4], opacity=0.5).encode(y="y:Q")
    return ((zero + line).properties(height=260, background="transparent")
            .configure_view(fill=None, strokeWidth=0))


def _fmt_pnl(v):
    return "—" if v is None else (f"+${v:,.2f}" if v >= 0 else f"−${abs(v):,.2f}")


def render():
    market_view._inject_theme(market_view._seed_theme())
    st.title("My Bets")

    try:
        rows, summ, curve = _load_bets()
    except kalshi_auth.KalshiCredentialsError:
        st.info("Add your Kalshi API key to the app secrets to enable this page — "
                "a `[kalshi]` section with `access_key_id` and `private_key`. "
                "The key is read only from secrets and used for read-only requests.")
        return
    except Exception as e:                       # never crash the dashboard
        st.warning(f"Couldn't load your Kalshi bets right now ({type(e).__name__}). "
                   "The rest of the dashboard is unaffected; try again shortly.")
        return

    if not rows:
        st.caption(f"No Dallas-temp bets found since {bet_history.BETS_START:%b %-d, %Y}.")
        return

    c = st.columns(5)
    c[0].metric("Record (W–L)", f"{summ['wins']}–{summ['losses']}")
    c[1].metric("Win rate", f"{summ['win_rate']:.0f}%")
    c[2].metric("Net P&L", _fmt_pnl(summ["net_pnl"]))
    c[3].metric("ROI", f"{summ['roi']:+.0f}%")
    c[4].metric("Bets with model",
                "—" if summ["with_model_pct"] is None else f"{summ['with_model_pct']:.0f}%")

    if curve:
        st.altair_chart(equity_chart(curve, market_view._chart_colors()["kalshi"]),
                        use_container_width=True)
    else:
        st.caption("The equity curve appears once a bet settles.")

    disp = []
    for r in rows:
        model = "—" if r.get("model_prob") is None else (
            f"{r['model_prob']*100:.0f}% · {r['edge']*100:+.0f} · "
            + ("with" if r["agree"] else "against"))
        disp.append({
            "Date": r["first_ts"].strftime("%b %-d"),
            "Contract": r["label"], "Side": r["side"].upper(),
            "Entry": market_view.cents(r["entry"]), "Qty": r["qty"],
            "Model @ bet": model,
            "Settled": "open" if r["status"] == "open" else r["result"].upper(),
            "P&L": _fmt_pnl(r["pnl"]),
        })
    market_view._html_table(pd.DataFrame(disp))
    st.caption("Model @ bet = the model's probability for the side you took, its "
               "edge vs your entry (pp), and whether you bet with or against it — "
               "reconstructed from the nearest logged snapshot to your fill (— if "
               "none). P&L is realized on settlement. Read-only view of your Kalshi "
               "account; prices in ¢, amounts in $.")
```

- [ ] **Step 5: Wire secrets seeding + the nav page in `app.py`**

In `app.py`, extend the existing `[github]` secrets block to also seed the Kalshi env. After the `if _gh:` block (around line 34), add:

```python
try:
    _kal = dict(st.secrets["kalshi"]) if "kalshi" in st.secrets else None
except Exception:
    _kal = None
if _kal:
    os.environ.setdefault("KALSHI_ACCESS_KEY_ID", _kal.get("access_key_id", ""))
    os.environ.setdefault("KALSHI_PRIVATE_KEY", _kal.get("private_key", ""))
```

Then change the navigation (the `st.navigation([...])` call near the end) to add the second page:

```python
import bet_view

st.navigation([
    st.Page(kalshi_page, title="Kalshi", default=True),
    st.Page(bet_view.render, title="My Bets"),
]).run()
```

- [ ] **Step 6: Run the test + full suite + import smoke**

Run: `cd "/Users/jared/Desktop/Weather Model" && .venv/bin/python -c "import app, bet_view" && .venv/bin/python -m pytest tests/ -q`
Expected: imports succeed; full suite passes (including the four new test files).

- [ ] **Step 7: Manual verification (needs the user's Kalshi key — do NOT block on it)**

The page's live behavior can only be verified with real credentials, which only the user has. In your report, mark this deferred to the user on the deployed app, and note the exact checks:
- With no `[kalshi]` secret set: the page shows the enable-note and the rest of the app is unaffected.
- With the key set: summary strip populates, the equity curve renders (x=date, y=cumulative $), and the table lists Dallas-temp bets since Jun 22 with Model @ bet annotations.
- **Confirm the assumed Kalshi field names** (Global Constraints) against a real fills/settlements response; if any differ (e.g. price units, `market_result` vs `result`), adjust `kalshi_portfolio` normalization and its test.

- [ ] **Step 8: Commit**

```bash
cd "/Users/jared/Desktop/Weather Model"
git add bet_view.py app.py requirements.txt tests/test_bet_view.py
git commit -m "feat: My Bets page (equity curve + annotated bet history) + wiring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Read-only is a hard boundary.** Nothing in this plan issues a non-GET Kalshi request. If you find yourself reaching for an order endpoint, stop — it's out of scope.
- **`cryptography` install:** Tasks 1–4 import it; run Task 5 Step 1's `pip install` first if your venv doesn't already have it (it's harmless to run early).
- **Field-name risk:** the Kalshi JSON schema in Global Constraints is from the docs, not a captured live response. The pure code + tests are internally consistent; Task 5 Step 7 is where you reconcile with reality. Keep the normalization (in `kalshi_portfolio`) the single place that knows Kalshi's raw field names.
- **σ fallback:** when a fill matches only a `consensus_history` sample (no `sigma_used`), the reconstruction uses `calib["sigma"][variable]` (the day-ahead σ). That's an approximation, intentionally — the annotation is best-effort by design.
```
