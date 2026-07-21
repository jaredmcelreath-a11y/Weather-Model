"""ntfy push helper."""
import notify


def test_send_ntfy_noop_without_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert notify.send_ntfy("t", "m") is False


def test_send_ntfy_posts_with_title_and_body(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "my-secret-topic")
    calls = {}

    class _Resp:
        def raise_for_status(self):
            pass

    def fake_post(url, data=None, headers=None, timeout=None):
        calls["url"] = url
        calls["data"] = data
        calls["headers"] = headers
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    assert notify.send_ntfy("Dallas Climate Report", "High 100") is True
    assert calls["url"] == "https://ntfy.sh/my-secret-topic"
    assert calls["headers"]["Title"] == "Dallas Climate Report"
    assert b"High 100" == calls["data"]


def test_send_ntfy_swallows_errors(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.requests, "post", boom)
    assert notify.send_ntfy("t", "m") is False
