import pytest

from sources import kalshi_orders as ko


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, body=None, timeout=10):
        self.calls.append((method, path, body))
        return {"order": {"order_id": "OID1", "status": "resting"}}


def test_shadow_places_no_network_call():
    t = FakeTransport()
    ack = ko.place_order(ticker="KXHIGHTDAL-26JUL24-B99", side="yes", action="buy",
                         count=1, price=0.60, client_order_id="cid-1",
                         mode="shadow", transport=t)
    assert t.calls == []                      # no network in shadow
    assert ack["shadow"] is True
    assert ack["client_order_id"] == "cid-1"


def test_live_posts_order_with_cents_price():
    t = FakeTransport()
    ack = ko.place_order(ticker="KXHIGHTDAL-26JUL24-B99", side="yes", action="buy",
                         count=2, price=0.60, client_order_id="cid-2",
                         mode="live", transport=t)
    method, path, body = t.calls[0]
    assert method == "POST" and path == "/portfolio/orders"
    assert body["count"] == 2
    assert body["yes_price"] == 60           # dollars -> whole cents
    assert body["client_order_id"] == "cid-2"
    assert ack["order"]["order_id"] == "OID1"


def test_no_price_used_for_no_side():
    t = FakeTransport()
    ko.place_order(ticker="X", side="no", action="buy", count=1, price=0.30,
                   client_order_id="c", mode="live", transport=t)
    _m, _p, body = t.calls[0]
    assert body["no_price"] == 30 and "yes_price" not in body


def test_positions_normalizes_series_only():
    from sources import kalshi_portfolio as kp

    def fake_get(path, params=None):
        return {"market_positions": [
            {"ticker": "KXHIGHTDAL-26JUL24-B99", "position": 2},
            {"ticker": "KXOTHER-1", "position": 5},
        ]}

    out = kp.positions(fetch=fake_get)
    assert len(out) == 1
    assert out[0]["ticker"].startswith("KXHIGHTDAL")
    assert out[0]["count"] == 2
    assert out[0]["variable"] == "high"
