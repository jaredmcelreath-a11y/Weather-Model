import json

import trade_state as ts


class FakeTransport:
    """In-memory stand-in for the GitHub contents API, keyed by path."""
    def __init__(self):
        self.files = {}       # path -> (text, sha)
        self._n = 0

    def get(self, path):
        return self.files.get(path)          # (text, sha) or None

    def put(self, path, text, sha):
        if path in self.files and self.files[path][1] != sha:
            raise ts.ConflictError(path)
        self._n += 1
        self.files[path] = (text, f"sha{self._n}")


def test_load_state_absent_returns_defaults():
    t = FakeTransport()
    st = ts.load_state(transport=t)
    assert st["kill_switch"] is False        # ships active…
    assert st["mode"] == "shadow"            # …but shadow = no real orders


def test_save_then_load_roundtrip():
    t = FakeTransport()
    ts.save_state({"kill_switch": False, "mode": "live", "max_price": 0.9}, transport=t)
    st = ts.load_state(transport=t)
    assert st["kill_switch"] is False
    assert st["mode"] == "live"
    assert st["max_price"] == 0.9
    assert st["min_resolved"] == 0.70      # default filled by merge_params


def test_runtime_roundtrip():
    t = FakeTransport()
    assert ts.load_runtime(transport=t) == {}
    ts.save_runtime({"halt_day": "2026-07-24", "entries": {}}, transport=t)
    assert ts.load_runtime(transport=t)["halt_day"] == "2026-07-24"


def test_append_jsonl_accumulates():
    t = FakeTransport()
    ts.append_jsonl("trade_log.jsonl", {"a": 1}, transport=t)
    ts.append_jsonl("trade_log.jsonl", {"a": 2}, transport=t)
    text, _sha = t.get("trade_log.jsonl")
    lines = [json.loads(x) for x in text.splitlines() if x]
    assert [r["a"] for r in lines] == [1, 2]
