# Kalshi Autonomous Trading Loop — Design

**Date:** 2026-07-24
**Status:** Approved, ready for planning

## Goal

Add a **fully autonomous** trading loop that buys (and, on exit conditions, sells)
Kalshi KDFW temperature contracts on its own, governed entirely by a set of
live-editable parameters. Today the Kalshi integration is **read-only by design**
(`sources/kalshi_auth.py` issues GET only); this adds the first write path plus the
orchestration, state, logging, control UI, and guardrails around it.

The decision engine already exists and is **reused as-is**: `kelly.py` (book-aware
sizing, EV, fees), `model.py` (consensus + all safety gates), `sources/kalshi.py`
(orderbook, asks, market-implied forecast). This project adds the *execution* layer,
not new forecasting.

## Non-negotiable rollout gate

Ships in **`shadow` mode first.** The real loop runs against live signals and logs
every order it *would* place, without hitting the network. After a validation window
(~1–2 weeks, user's call), the user reviews the shadow log and manually flips
`mode → live`. **No real dollars move until that switch is thrown.**

## Architecture — the run loop

A dedicated GitHub Actions workflow (`.github/workflows/trade.yml`) runs `trader.py`
on a tight schedule, **KDFW market-hours only**. It is separate from the 10-min
`log.yml` logger so trading failures never affect logging. Streamlit is **not** an
execution surface — it only writes parameters.

Every run is idempotent and **reconciles truth from the Kalshi account**, never from
a local ledger (GitHub cron drifts, skips, and can overlap; the account is the only
reliable source of what is held):

```
trade.yml (fast cron, market hours)
        │
        ▼
   trader.py  ── one run = one pass
        │
        ├─ 1. Load params + kill switch from trade_state (data branch)
        │       └─ kill switch OFF or outside market window → exit immediately
        ├─ 2. Reconcile truth from Kalshi (read client):
        │       positions, resting orders, balance
        │       └─ read fails → DO NOTHING (never trade blind)
        ├─ 3. Pull model signals for today's brackets (model.py):
        │       consensus, market-implied, resolved%, gates
        │       (low_forming, peak_locked, storm, quorum)
        ├─ 4. EXIT pass  → for each held position: stop-loss or reversal?
        │                   → stage sell orders
        ├─ 5. ENTRY pass → for each bracket passing all gates + caps
        │                   → size via kelly → stage buy orders
        ├─ 6. Execute staged orders via kalshi_orders.py
        │       (shadow mode → log only, no network)
        └─ 7. Append every decision + order + skip-reason to trade_log
```

Exit pass runs **before** entry so freed capital and reversals settle first.

**Cadence reality:** GitHub cron bottoms out around once per ~1–5 min and can skip
runs. That is fine for KDFW temp markets (edges persist over minutes) and
reconcile-from-truth makes skips harmless. This is **not** a sub-minute scalper and
must not be tuned like one.

## Components

### New files

- **`sources/kalshi_orders.py`** — the **only** module that can place/cancel orders.
  Adds `signed_request()` (RSA-PSS signing for POST/DELETE, mirroring
  `kalshi_auth.py`) plus `place_order()`, `cancel_order()`, `get_positions()`,
  `get_resting_orders()`. Every order entry point checks the kill switch and shadow
  flag first; in shadow mode `place_order()` logs and returns a synthetic ack with no
  network call. `kalshi_auth.py` stays read-only and untouched.
- **`trader.py`** — the orchestrator (run loop above). Pure decision logic; delegates
  signing to the clients, signals to `model.py`. No signing code of its own.
- **`trade_state.py`** — reads/writes the params + kill-switch state file on the data
  branch. Single shared schema for the Streamlit page (writer) and the cron (reader).
- **`trade_log.py`** — append-only audit log (decisions, staged orders, fills,
  skips-with-reason) to the data branch, following the `betting_log.py` /
  `scheduled_log.py` pattern.
- **`trade_view.py`** — Streamlit control page: kill switch, enable/disable,
  shadow↔live toggle, parameter sliders, live position/P&L table, recent-decision log.
- **`.github/workflows/trade.yml`** — the market-hours cron.

### Modified files

- **`app.py`** — register the new nav page; seed the Kalshi **write** secret into env
  (same pattern as the existing read secret).
- **`config.py`** — default parameter values + KDFW market-window constants.

### Reused unchanged

`kelly.py`, `model.py`, `sources/kalshi.py`, `notify.py`.

## Parameters (the "given parameters")

All live-editable from the control page; written to `trade_state` on the data branch,
read by the cron each run.

### Master switches
- `kill_switch` (bool) — hard stop; checked first every run.
- `mode` — `shadow` | `live`. Shadow logs intended orders, never sends.
- `enabled_variables` — HIGH, LOW, or both.

### Entry gating (agreement-based, NOT edge-based)
Entry is a **confirmation** strategy, not a disagreement/edge strategy — matching the
project's own finding that wins came from being *with* the market, not from edge.

- `min_resolved = 0.70` — no entry in a variable until it is ≥70% resolved.
- `agreement_tol = 1.0` (°F) — entry **requires** model consensus and market-implied
  center to agree within 1°F. If they disagree by more than 1°F, **wait**.
- `require_gates_clear = true` — respect `low_forming`, `peak_locked`, storm watch,
  and member-quorum. Keeps it out of a still-forming dawn low, etc.
- `max_price = 0.94` — do **not** enter if the ask is ≥ 94¢ (too little return left).
- `min_price` — floor (e.g. 0.10) below which fees/slippage dominate; a light price
  sanity check. `min_edge` is demoted to this price-bound sanity check only; it is not
  the entry trigger.

### Bracket selection (the near-tie tie-break rule)
When the agreed temp sits between two brackets (e.g. consensus/market ≈ 99.5 with
brackets 98–99 and 100–101), buy the bracket **in the direction the variable can
still move**:

- **HIGH** not yet peaked → buy the **upper** straddled bracket.
- **LOW** still forming / can still fall → buy the **lower** straddled bracket.

Rationale: an adverse excursion then moves *toward* the chosen bracket, so the
position degrades gradually and is **sellable via stop-loss** — whereas the opposite
bracket would settle to $0 the instant temp clears it (instant, unsellable loss).

### Sizing & exposure
- `per_market_cap = $0.50` — hard ceiling per bracket (testing size). Stored in
  dollars, converted to whole contracts at order time.
- `kelly_fraction` — fraction of full Kelly (e.g. 0.25), clamped by `per_market_cap`.
- `max_open_per_variable = 1` — at most one open position for HIGH and one for LOW
  (≤ 2 open total). Enforced against **reconciled** holdings.
- `daily_loss_cap` — **default ON (disable-able)**. Halts all trading for the rest of
  the climate day if realized+unrealized P&L drops below the cap. The primary
  runaway/bug circuit breaker. The user did not request it; it is included as a
  recommended default and may be set to `off`.

### Exit (stop-loss + reversal; winners hold to settlement)
No take-profit — winning positions **ride to Kalshi settlement**. Exit only on:

- **Stop-loss, measured on the ASK, referenced to entry ask** — never the bid/fill.
  - `entry_ask` = the ask the position was bought at.
  - Monitor the **current ask** on the held side (not the bid).
  - Trigger: `current_ask ≤ entry_ask − stop_loss`.
  - Why ask, not bid: watching the bid would trip the stop instantly on the bid-ask
    **spread** the moment the order fills. Watching the ask trips only on a genuine
    downward **repricing** of the market.
  - **Trigger-on-ask, fill-at-bid:** the ask crossing the threshold is the *trigger*;
    the actual exit is a marketable sell that fills into the current **bid**.
- **Signal reversal** — sell when the agreed-upon target bracket flips to a different
  bracket than the one held, or a safety gate fires against the held side.
- Otherwise → **hold to settlement.**

### Re-entry after a stop-loss
A stop-loss does **not** lock the variable for the day. Re-entry is allowed **only
when the signal has genuinely flipped** — the newly agreed target bracket is a
*different* bracket than the one just stopped out of. The same target bracket
re-appearing does **not** re-buy (prevents churning in and out during chop).

### Execution mechanics
- `market_open` / `market_close` — KDFW local trading window; outside it the cron
  no-ops on entries.
- `slippage_cap` — max cents through the ask a marketable-limit buy may pay.

## Execution mechanics (detail)

- **Write path:** `signed_request()` in `sources/kalshi_orders.py` (RSA-PSS, mirrors
  the read client) hitting `POST /portfolio/orders` and
  `DELETE /portfolio/orders/{id}`. The **exact request schema** (price in cents,
  `side`/`action`/`count`/`type`/`client_order_id`, expiration) is **verified against
  Kalshi's live API docs at implementation time** — not hardcoded from a guess here.
- **Order type:** **marketable limit**, not raw market. Buys priced at the current ask
  capped by `slippage_cap`, bounding fill price and never chasing a thin book past the
  cap. `kelly.py` already walks the ask ladder for true cost.
- **Idempotency:** each intended order carries a deterministic `client_order_id` =
  hash of (ticker, climate-day, intent, run-bucket). A duplicate from an overlapping
  or retried run is rejected by Kalshi, never double-filled.
- **Secrets:** a new **write-scoped** Kalshi API key in Streamlit/Actions secrets,
  separate from the read key where Kalshi permits, seeded to env in `app.py` and the
  workflow. Private key never logged, printed, or placed in an exception message
  (same discipline as `kalshi_auth.py`).

## Safety & guardrails

Checked top-to-bottom every run; any failure = no order + logged reason + (for halts)
a `notify` push alert:

1. **Kill switch** off → immediate exit, no order network calls.
2. **Market window** — outside KDFW hours → no entries.
3. **Mode** — `shadow` routes every order to the log, never the network.
4. **Reconcile-from-truth** — pull live positions/orders/balance; read failure → do
   nothing (never trade blind).
5. **Per-market cap** `$0.50` and **one-position-per-variable**, enforced against
   reconciled holdings.
6. **`daily_loss_cap`** (default ON) — halts the day if breached.
7. **Idempotency** — deterministic `client_order_id`.
8. **Model gates** — `low_forming`, `peak_locked`, storm, quorum, `min_resolved`,
   `agreement_tol`, `max_price`.

## Testing

TDD throughout, following the existing suite:

- **Pure-function decision core** (agreement gate, bracket tie-break for HIGH and LOW,
  stop-loss-on-ask, reversal, re-entry-flip, sizing/caps) tested with synthetic
  orderbooks + model states — no network.
- **`kalshi_orders.py`** tested against a **mocked signed transport**: a bad signature
  or unexpected schema must **raise**, never silently no-op. Shadow mode verified to
  place zero network calls.
- **Idempotency** — same run-bucket + inputs yields the same `client_order_id` and
  no second order.
- **Reconcile-from-truth** — caps and one-per-variable enforced from mocked Kalshi
  positions, including the skipped-run / desync case.

## Rollout

1. Ship in `shadow` mode; run against live signals ~1–2 weeks.
2. User reviews the shadow trade log on the control page.
3. User flips `mode → live`. `per_market_cap` stays at `$0.50` for the initial live
   test.

## Out of scope (YAGNI)

- Take-profit / price-target selling (winners hold to settlement).
- Multi-station or non-KDFW markets.
- Sub-minute / scalping cadence.
- Auto-tuning of parameters (all manual via the control page).
