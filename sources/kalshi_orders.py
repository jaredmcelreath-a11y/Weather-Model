"""WRITE-capable Kalshi client — the ONLY module that can place or cancel orders.

Deliberately separate from sources/kalshi_auth.py (which stays read-only) so the
dangerous capability lives in one auditable place. Signs with the same RSA-PSS
scheme. In shadow mode place_order/cancel_order make NO network call — they log
and return a synthetic ack — so the whole pipeline can run against live signals
without moving money. Private key material is never logged or put in an exception.

Order body confirmed against the Kalshi Trade API v2 create-order docs (2026-07):
POST /trade-api/v2/portfolio/orders with ticker/action/side/type/count and a
whole-cent yes_price|no_price; cancel is DELETE /portfolio/orders/{order_id}.
"""
from __future__ import annotations

import requests

from sources import kalshi_auth


def signed_request(method: str, path: str, body: dict | None = None,
                   timeout: int = 10) -> dict:
    """RSA-PSS-signed request to the trade API. `path` is the sub-path after the
    API prefix, e.g. '/portfolio/orders'. Returns parsed JSON; raises for HTTP
    errors. This is the sole write path in the codebase."""
    key_id, private_key = kalshi_auth.load_credentials()
    full_path = kalshi_auth.API_PREFIX + path
    headers = kalshi_auth.auth_headers(method, full_path, key_id, private_key)
    headers["Content-Type"] = "application/json"
    resp = requests.request(method, kalshi_auth.HOST + full_path, json=body,
                            headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _order_body(ticker, side, action, count, price, client_order_id) -> dict:
    """Kalshi order body. Price is dollars 0-1 -> whole cents 1-99."""
    cents = int(round(price * 100))
    body = {
        "ticker": ticker,
        "client_order_id": client_order_id,
        "side": side,               # "yes" | "no"
        "action": action,           # "buy" | "sell"
        "count": count,
        "type": "limit",
    }
    body["yes_price" if side == "yes" else "no_price"] = cents
    return body


def place_order(*, ticker, side, action, count, price, client_order_id, mode,
                transport=None) -> dict:
    """Place one marketable-limit order. Shadow mode → log + synthetic ack, no
    network. `transport` is injectable for tests (defaults to signed_request)."""
    body = _order_body(ticker, side, action, count, price, client_order_id)
    if mode != "live":
        print(f"[SHADOW] would {action} {count} {side} {ticker} @ {price:.2f} "
              f"(cid={client_order_id})")
        return {"shadow": True, "client_order_id": client_order_id, "body": body}
    call = transport or signed_request
    return call("POST", "/portfolio/orders", body)


def cancel_order(order_id: str, mode: str, transport=None) -> dict:
    if mode != "live":
        print(f"[SHADOW] would cancel {order_id}")
        return {"shadow": True, "order_id": order_id}
    call = transport or signed_request
    return call("DELETE", f"/portfolio/orders/{order_id}")


def new_client_order_id(ticker: str, day_iso: str, intent: str, bucket: str) -> str:
    """Deterministic idempotency key for one (ticker, day, intent, run-bucket).
    A retried/overlapping run yields the same id, so Kalshi rejects the dup."""
    return f"{ticker}:{day_iso}:{intent}:{bucket}"
