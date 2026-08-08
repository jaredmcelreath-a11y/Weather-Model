"""Whole-file JSON documents on the scan-data branch (reference + alert state)."""
import json

import scan_log


class FakeTransport:
    def __init__(self, text=None, sha="sha1"):
        self.store = None if text is None else (text, sha)
        self.puts = []

    def get(self, path):
        return self.store

    def put(self, path, text, sha):
        self.puts.append((path, text, sha))
        self.store = (text, "sha2")


def test_read_doc_parses_the_document():
    t = FakeTransport(json.dumps({"generated": "2026-08-07T18:30:00Z"}))
    assert scan_log.read_doc("x.json", t)["generated"] == "2026-08-07T18:30:00Z"


def test_read_doc_is_empty_when_absent():
    assert scan_log.read_doc("x.json", FakeTransport(None)) == {}


def test_read_doc_survives_corrupt_content():
    # A half-written document must read as "nothing yet", never raise: this runs
    # in a cron whose whole job is to be quietly reliable.
    assert scan_log.read_doc("x.json", FakeTransport("{not json")) == {}


def test_read_doc_rejects_a_non_object():
    assert scan_log.read_doc("x.json", FakeTransport("[1, 2]")) == {}


def test_write_doc_replaces_and_carries_the_sha():
    t = FakeTransport(json.dumps({"a": 1}))
    scan_log.write_doc("x.json", {"b": 2}, t)
    path, text, sha = t.puts[0]
    assert path == "x.json"
    assert json.loads(text) == {"b": 2}
    assert sha == "sha1"          # the contents API needs the prior sha


def test_write_doc_creates_a_missing_file():
    t = FakeTransport(None)
    scan_log.write_doc("x.json", {"b": 2}, t)
    assert t.puts[0][2] is None


def test_the_document_paths_are_named():
    assert scan_log.REFERENCE_PATH == "screen_reference.json"
    assert scan_log.ALERT_STATE_PATH == "screen_alert_state.json"
