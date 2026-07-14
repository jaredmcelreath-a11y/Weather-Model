# Kelly Sizing Helper — Design

**Date:** 2026-07-14
**Status:** Approved (design), pending implementation plan

## Problem

The dashboard already tells the user *which* Kalshi temp-bucket contracts the model
thinks are underpriced (edge, EV/cost, Top-3 Hold-to-Settlement, Safest Hold). It does
not tell the user *how much to bet*. Kelly criterion answers that — but a naive Kelly
number ignores a real constraint the user cares about: **returns shrink as you bet more**.
That shrinkage is order-book slippage — buying more contracts eats through the cheap
asks and climbs to pricier ones, dragging down the average fill price and therefore the
edge on the marginal contract. The helper must size bets so it stops adding contracts
once the extra ones stop being worth it.

## Core insight

A single Kalshi temperature bucket is **one binary bet**: every contract in that bucket
shares the same outcome — the day's temp either lands in the bucket (each contract
settles $1) or it does not (each settles $0). So this is Kelly on a binary bet, but with
a **lumpy, size-dependent cost curve** rather than a single fixed price.

## Math (`kelly.py` — new module, pure functions)

Notation: model win-probability `q` (0–1), bankroll `B` (dollars), contract prices in
dollars (0–1). We buy `N` contracts by walking the **ask ladder** — a list of
`(price, size)` levels sorted ascending — accumulating cost `C(N)` (the sum of the `N`
cheapest asks, plus fees).

### Classic Kelly (single price, reference point)
For a contract bought at fixed price `p`, the Kelly fraction of bankroll to put at risk is:

```
f* = (q − p) / (1 − p)
```

Number of contracts at a flat price = `f*·B / p`. This is the degenerate case the book
walk generalizes.

### Kelly with the book walk (the real model)
Choose the integer number of contracts `N ≥ 0` that maximizes expected log-growth of
bankroll:

```
G(N) = q·ln(B + N − C(N)) + (1 − q)·ln(B − C(N))
```

- **Win** (prob `q`): the `N` contracts pay $1 each, so bankroll → `B − C(N) + N`.
- **Loss** (prob `1 − q`): the contracts expire worthless, bankroll → `B − C(N)`.

### Marginal EV and the hard ceiling
The marginal `N`-th contract costs the next ask `a_N`. Its expected value in dollars is:

```
marginalEV(N) = q·(1 − a_N) − (1 − q)·a_N − marginalFee(N)
             = (q − a_N) − marginalFee(N)
```

**Once `marginalEV(N) ≤ 0` — i.e. the marginal ask (plus fee) reaches the model's win
probability — the next contract is a losing bet and must never be bought.** This is the
absolute ceiling `N_max`, and it is exactly the "adding more isn't worth it" line. It
falls straight out of the book walk; nothing extra is needed to detect it.

### Fractional Kelly (user slider λ, default 0.5)
Full Kelly (`N*` = argmax `G(N)`) is aggressive and assumes `q` is exactly right, which
the model's day-ahead calibration (~50–60% exact-bin) does not support. Fractional Kelly
scales the stake:

1. Find full-Kelly optimum `N*` and its cost `C* = C(N*)`.
2. Target stake = `λ · C*`.
3. **Recommended `N` = largest N with `C(N) ≤ λ·C*`.**

At `λ = 1` this recovers full Kelly. Because fractional Kelly only ever *reduces* the
stake, the recommendation always sits at or below `N*`, which itself sits below `N_max`
— so a fractional recommendation can never cross the negative-EV ceiling.

### Fees (in scope for v1)
Kalshi charges a trading fee that is folded into `C(N)` and `marginalFee(N)`:

```
fee(N contracts at price p) = ceil_to_cent( 0.07 · N · p · (1 − p) )
```

The fee is roughly proportional to contract count but scaled by `p·(1−p)`, so it acts
like a per-contract surcharge that lowers each contract's effective payout and pulls the
stopping point `N_max` inward. It is isolated in a single `fee()` function so the formula
can be updated or disabled without touching the sizing logic. Marginal fee at contract
`N` is `fee(N) − fee(N−1)`.

### Edge cases (defined behavior)
- **Best ask already ≥ q** (`f* ≤ 0`): recommend **0 contracts** — "no bet: market ask
  already ≥ model probability."
- **Thin book**: `N` is capped by available depth; surface "limited by book depth — only
  X contracts available at a positive edge."
- **Bankroll constraint**: enforce `C(N) ≤ B`.

### Functions
- `kelly_fraction(q, price) -> float` — classic single-price fraction (reference/tests).
- `fee(n, price) -> float` — Kalshi trading fee in dollars for `n` contracts at `price`.
- `cost_to_buy(ladder, n) -> float` — total cost (incl. fees) to acquire `n` contracts
  by walking the ask ladder; `None`/cap when depth is insufficient.
- `optimal_size(ladder, q, bankroll, kelly_frac, ...) -> Sizing` — the crux. Returns a
  small result object: recommended `N`, average fill price, stake, expected value,
  full-Kelly `N*`, negative-EV ceiling `N_max`, and a per-N EV/return series for the
  chart.

## Order-book data (`sources/kalshi.py`)

New `fetch_orderbook(ticker) -> list[(price, size)]` hitting Kalshi's
`/markets/{ticker}/orderbook`, normalized into an **ascending ask ladder** for the side
being bought (YES or NO). Kalshi's book convention is fiddly — the YES ask ladder is
reconstructed from resting NO bids (and vice-versa) — so, consistent with how this repo
already verifies settlement bases empirically, the exact field mapping will be
**confirmed against the live endpoint during implementation**, with a captured JSON
fixture committed for tests. Cached ~10–15s via the existing `get_json` TTL mechanism,
and fetched **only when a contract is selected** in the helper — not for every contract
on page load, to keep API load down.

## UI (`market_view.py` — standalone bordered box, per market)

A self-contained section on each market (high / low):

- **Contract dropdown** — the live buckets for that market.
- **Bankroll input** — auto-filled from the live Kalshi cash balance via
  `kalshi_portfolio.balance()`, with an editable manual override. Falls back to manual
  entry when creds are absent or the fetch fails.
- **Kelly-fraction slider** — 0.25×–1.0×, default 0.5×.
- **Result** — recommended contracts, average fill price, total stake, expected value in
  dollars; plus the two reference points ("adding past N goes negative-EV").
- **Size-vs-return chart** — an Altair curve of EV (or return %) versus number of
  contracts, marking the Kelly-recommended `N` and the absolute EV=0 ceiling `N_max`, so
  the point where extra contracts stop being worth it is visible at a glance.
- **Empty states** — "no bet" when the ask already ≥ model prob; "limited by book depth"
  when the book is thin; manual bankroll entry when creds are missing.

The UI is thin: it calls `kelly.optimal_size`, `kalshi.fetch_orderbook`, and
`kalshi_portfolio.balance()`, and renders. No sizing logic lives in the view.

## Testing (`tests/test_kelly.py`)

The math is pure and deterministic — the primary test surface:

- `kelly_fraction` against hand-computed values (incl. `q ≤ p` → ≤ 0).
- `fee` against Kalshi's published fee examples.
- `cost_to_buy` walking a synthetic ladder — verify it climbs levels correctly, incl.
  the preview example `55¢×40 / 58¢×120 / 63¢×300` (buy 40 → avg 55¢, 100 → 57.2¢,
  300 → 60.9¢) plus fees.
- `optimal_size` — recommended `N`, avg price, EV, `N*`, and `N_max` on synthetic
  ladders; fractional-Kelly monotonicity (larger λ ⇒ N nondecreasing, λ=1 ⇒ full Kelly);
  the negative-EV ceiling is never crossed; edge cases (ask ≥ q, thin book, bankroll cap).
- Book normalization from a captured `/orderbook` fixture into the ascending ask ladder.

## Out of scope (YAGNI)

- Sizing across multiple correlated buckets as a portfolio (each bucket is sized
  independently for v1).
- Order placement / execution — the helper recommends, it does not trade.
- The "Suggested size" column on the Top-3 table (standalone box only for v1).
