"""The lock-status badge must read the monotonic `resolved` field (the same one
the metric card shows), not recompute a number from the noisy `locked_ratio`.

Before this, the badge's success gate used int((1 - locked_ratio) * 100), which
spikes and crashes through the day — so the green "prime buy window" badge could
flash then retract even while the metric card above it moved monotonically.
"""
import sys
from unittest.mock import MagicMock

try:  # let the display module import without a real streamlit locally
    import streamlit  # noqa: F401
except ImportError:
    for _m in ("streamlit", "streamlit.components", "streamlit.components.v1",
               "streamlit_autorefresh"):
        sys.modules.setdefault(_m, MagicMock())

from market_view import lock_status


def _high(**over):
    d = {"locked_ratio": 0.5, "resolved": 0.9, "observed_so_far": 95.0,
         "consensus": 95.0, "sigma_used": 0.8, "peak_locked": False}
    d.update(over)
    return d


def _low(**over):
    d = {"locked_ratio": 0.5, "resolved": 0.9, "observed_so_far": 77.0,
         "consensus": 77.0, "sigma_used": 0.8, "peak_locked": False,
         "convective_widened": False, "front_widened": False}
    d.update(over)
    return d


def test_high_badge_success_follows_monotonic_resolved():
    # monotonic resolved 90% -> success, even though 1-locked_ratio is only 50%
    level, headline, _ = lock_status(_high(), "high")
    assert level == "success" and "Locked" in headline


def test_high_badge_not_fooled_by_transient_locked_ratio():
    # locked_ratio momentarily low (1-lr=95%) but monotonic resolved 40% -> not settled
    level, _, _ = lock_status(_high(locked_ratio=0.05, resolved=0.4), "high")
    assert level == "info"


def test_low_badge_success_follows_monotonic_resolved():
    level, headline, _ = lock_status(_low(), "low")
    assert level == "success" and "Locked" in headline


def test_badge_percent_text_uses_monotonic_resolved():
    _, _, detail = lock_status(_high(locked_ratio=0.5, resolved=0.6), "high")
    assert "60%" in detail and "50%" not in detail


def test_badge_falls_back_to_locked_ratio_without_resolved_field():
    d = _high(locked_ratio=0.05)
    del d["resolved"]                       # older snapshot, no monotonic field
    level, _, _ = lock_status(d, "high")
    assert level == "success"               # falls back to 1 - locked_ratio (95%)
