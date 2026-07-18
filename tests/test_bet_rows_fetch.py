"""bet_history.fetch_rows — the one shared 'live bet rows' builder (recap,
portfolio value, Journal). Lazy-imports the Kalshi client so this module stays
importable without cryptography."""
import sys
from datetime import date
from unittest.mock import MagicMock

import bet_history


def test_fetch_rows_builds_and_annotates(monkeypatch):
    stub = MagicMock()
    stub.fills.return_value = []
    stub.settlements.return_value = []
    fake_sources = MagicMock()
    fake_sources.kalshi_portfolio = stub
    monkeypatch.setitem(sys.modules, "sources", fake_sources)
    monkeypatch.setitem(sys.modules, "sources.kalshi_portfolio", stub)
    out = bet_history.fetch_rows(date(2026, 6, 22))
    assert out == []
    stub.fills.assert_called_once()
