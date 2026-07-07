"""Unit tests for the Kalshi RSA-signing client. No network — signing and header
construction are pure; a throwaway RSA key is generated per test and used to
verify the emitted signature."""

import base64
from unittest.mock import Mock, patch

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


def test_signed_get_issues_read_only_get_with_signed_headers(monkeypatch):
    _, pem = _keypair()
    monkeypatch.setenv("KALSHI_ACCESS_KEY_ID", "kid-xyz")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem)

    mock_resp = Mock()
    mock_resp.json.return_value = {"ok": True}
    mock_resp.raise_for_status = Mock()

    with patch("sources.kalshi_auth.requests.get", return_value=mock_resp) as mock_get:
        result = ka.signed_get("/portfolio/fills", params={"limit": 5})

    assert result == {"ok": True}
    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    url = args[0] if args else kwargs["url"]
    assert url == ka.HOST + ka.API_PREFIX + "/portfolio/fills"
    assert kwargs["params"] == {"limit": 5}
    headers = kwargs["headers"]
    assert "KALSHI-ACCESS-KEY" in headers
    assert "KALSHI-ACCESS-TIMESTAMP" in headers
    assert "KALSHI-ACCESS-SIGNATURE" in headers
