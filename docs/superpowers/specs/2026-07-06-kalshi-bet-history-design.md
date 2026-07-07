# Kalshi Bet History & Performance Page

**Date:** 2026-07-06
**Status:** Approved, ready for implementation plan

## Problem

The dashboard forecasts the KDFW daily high/low and shows the live Kalshi market,
but it has no view of the user's *actual* trades. The user wants a new page that
pulls their real Kalshi bets on the Dallas temperature markets, shows how each
settled (realized P&L), annotates each with what the model thought at the moment
they bet, and charts their cumulative profit over time like a stock chart.

## Scope

**In scope**
- Authenticated, **read-only** access to the user's Kalshi portfolio.
- Bet history (fills) on the Dallas temp series only: `KXHIGHTDAL` / `KXLOWTDAL`.
- Realized P&L on settled bets (from Kalshi's settlement records).
- Model-edge annotation *as of the time each bet was placed* (reconstructed from
  existing logs; "—" where no snapshot is available).
- All bets from **2026-06-22** onward (both the recent and historical Kalshi
  tiers), and growing going forward.
- A new **"My Bets"** page: an equity curve on top, the bet-history table beneath,
  a summary-stats strip above the curve.

**Explicitly out of scope**
- Live open-position tracking / unrealized mark-to-market (the user chose the
  history view, not a positions monitor).
- Placing, modifying, or cancelling orders. This feature only ever issues
  `GET /portfolio/*` and `GET /historical/*` requests.
- Non-Dallas-temp Kalshi markets.
- Account balance / deposit history (the equity curve is cumulative realized P&L
  from $0, which needs neither).

## Decisions (from brainstorming)

- **Data source:** Kalshi authenticated API (RSA-signed). The user generates an
  API key and adds it to app secrets themselves.
- **Content:** bet history + realized P&L + model-edge-at-bet-time. No live
  positions.
- **Market scope:** Dallas temp only.
- **Model edge timing:** at the time of the bet, reconstructed; "—" when no
  snapshot is near the fill.
- **Placement:** a new "My Bets" page in the sidebar nav.
- **History range:** all bets since 2026-06-22, via both the recent and historical
  Kalshi endpoints.
- **Equity curve:** cumulative realized P&L from $0, x = date, y = running total,
  styled like a stock chart, above the table.
- **Approach:** live-fetch + on-the-fly annotation (stateless — Kalshi is the
  source of truth; no new persistent store).

## Architecture (new, isolated units)

- `sources/kalshi_auth.py` — RSA-PSS request signer. Reads
  `KALSHI_ACCESS_KEY_ID` and `KALSHI_PRIVATE_KEY` from env. Exposes
  `signed_get(path, params=None)` returning parsed JSON. **Read-only** — it only
  builds GET requests; there is no order-placing code path. The private key is
  never logged or included in any error message.
- `sources/kalshi_portfolio.py` — normalized fetchers over the signed client:
  `fills(start)` and `settlements(start)`, each merging the recent
  (`/portfolio/…`) and historical (`/historical/…`) tiers, following cursor
  pagination, filtering to the Dallas temp series and `>= start`, returning plain
  dicts.
- `bet_history.py` (repo root, pure/testable) — `build(fills, settlements, logs)`
  joins fills + settlements into per-bet rows, computes realized P&L and summary
  stats, attaches model-at-bet-time, and emits the equity-curve series.
- `bet_view.py` — renders the "My Bets" page (reuses `market_view`'s theme inject
  and HTML-table helpers).
- `app.py` — seed `KALSHI_*` env from `st.secrets["kalshi"]`; add the second
  `st.Page`.
- `requirements.txt` — add `cryptography`.

## Authentication & security

Signing follows Kalshi's scheme: sign the string `timestamp_ms + "GET" + path`
(where `path` includes `/trade-api/v2` and excludes the query string) with
RSA-PSS, SHA-256, MGF1-SHA-256, salt length = 32 bytes, base64-encoded. Send:
`KALSHI-ACCESS-KEY` (the access key id), `KALSHI-ACCESS-TIMESTAMP` (the same ms
value), `KALSHI-ACCESS-SIGNATURE` (the signature).

Credentials come only from `st.secrets["kalshi"]` → `KALSHI_*` env, mirroring the
existing `[github]` seeding in `app.py`. The private key is never printed, logged,
or surfaced in an error string, and never passes through the assistant — the user
pastes it into Streamlit secrets. When credentials are absent, the page renders a
"add your Kalshi API key to `[kalshi]` secrets to enable this" note instead of
erroring.

## Data flow: fills + settlements → bets → P&L

1. `kalshi_portfolio.fills(BETS_START)` pulls from `/portfolio/fills` and
   `/historical/fills`, paging via cursor until exhausted or past `BETS_START`,
   filters to `KXHIGHTDAL`/`KXLOWTDAL` and `created_time >= BETS_START`, dedupes
   by fill/trade id, and normalizes each to:
   `{ticker, label, variable, floor, cap, side, action, price, count, ts}`.
2. `kalshi_portfolio.settlements(BETS_START)` similarly pulls
   `/portfolio/settlements` + `/historical/settlements`, keyed by market ticker,
   giving the settled result and Kalshi's own realized revenue per position.
3. `bet_history.build(...)` groups fills by ticker into bets, pairs each with its
   settlement, and reports realized P&L **from Kalshi's settlement record** (so
   partial-fill accounting isn't re-derived). `BETS_START = date(2026, 6, 22)` is
   a module constant.

Unsettled (open) bets appear in the table marked "open" with no P&L and do not
contribute to the equity curve until they settle.

## Model-at-bet-time reconstruction

For a fill at time *T* on a contract with range *[floor, cap]* of (date *D*,
variable *V*):

1. Find the nearest model snapshot to *T*, within a tolerance: first
   `betting_log` (has `cli_consensus` + `sigma_used` at afternoon slots), else
   `consensus_history` (consensus + a calibrated σ estimate from `calibration`).
2. Build a normal `N(consensus, σ)` and integrate over *[floor, cap]* (open-ended
   buckets use a one-sided tail) for the model's probability the contract settles
   YES.
3. `edge = model_prob − your_entry_price` for the side taken; a "with/against
   model" flag from its sign.
4. No snapshot within tolerance → model prob/edge render as "—".

The normal approximation matches how the model already communicates (consensus ±
1σ); it is an explicit reconstruction, not the exact historical blend.

## UI — new "My Bets" page

Top to bottom:

1. **Summary strip** (metric boxes): settled record (W–L), win rate, net realized
   P&L, ROI (net P&L ÷ total staked), and "% of bets placed with the model."
2. **Equity curve** — Altair line chart, x = date, y = cumulative realized P&L
   from $0, one step per settled bet (newest right), dark-themed to match, with a
   zero baseline. Reads like a stock chart.
3. **Bet-history table** (themed HTML, newest first):
   Date · Contract · Side · Entry · Qty · Model@bet (prob / edge / with·against) ·
   Settled (WON/LOST/open) · P&L.

## Error handling

Auth or API failures are caught and shown as a `st.warning`, never crashing the
dashboard (matching the source-outage-resilience pattern). Missing credentials →
the friendly enable-note. A malformed/empty response yields an empty ledger with a
caption, not an exception.

## Testing (synthetic, no network)

- `kalshi_auth`: signing a known string produces a signature that verifies against
  a test RSA public key, and emits the three required headers with the timestamp
  it signed. Missing-credential path raises a clear, key-free error.
- `kalshi_portfolio`: parses synthetic Kalshi fills/settlements JSON; filters to
  the Dallas series and `>= start`; follows a two-page cursor; dedupes across the
  recent + historical tiers.
- `bet_history`: `build(...)` produces correct per-bet rows and realized P&L;
  summary stats (win rate, net P&L, ROI, with-model %); reconstruction (nearest-
  snapshot selection, normal-CDF probability over a range, edge sign, "—" gap);
  and a correct cumulative equity-curve series (monotonic in date, steps by each
  settled bet's P&L).

## Non-goals / follow-ups

- Local enrichment log (snapshot the model read at fill time when first seen), for
  annotation that survives log rotation — deferred; only needed if reconstruction
  gaps prove annoying.
- Live positions / unrealized P&L — out of scope by choice.
- Order placement — intentionally never built.
