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
