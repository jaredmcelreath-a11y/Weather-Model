"""The lock badge/captions read the hybrid Resolved, not the current one."""
from market_view import lock_status


def _high(**over):
    d = {"observed_so_far": 100.0, "consensus": 100.0, "sigma_used": 0.7,
         "peak_locked": False, "locked_ratio": 0.1,
         "resolved": 0.10, "resolved_hybrid": 0.90}
    d.update(over)
    return d


def test_badge_uses_hybrid_high():
    # Hybrid 90% clears the 85% "locked" gate even though current is only 10%.
    level, headline, _ = lock_status(_high(), "high")
    assert level == "success"


def test_badge_stays_open_when_hybrid_low():
    # Hybrid 40% keeps it "locking" even though current is 99%.
    level, headline, _ = lock_status(_high(resolved=0.99, resolved_hybrid=0.40), "high")
    assert level == "info"
