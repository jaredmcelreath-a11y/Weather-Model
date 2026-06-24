"""Active NWS alerts feed (best-effort) for the convective-low trigger.

One call to api.weather.gov/alerts/active for a state; the caller scans the
returned features for a Severe Thunderstorm Warning intersecting the upstream
counties. Best-effort: any failure yields an empty feature list so a prediction
never breaks on the alerts API.
"""

from __future__ import annotations

from sources import common

ALERTS_URL = "https://api.weather.gov/alerts/active"


def fetch_active(area: str = "TX", ttl: int = 300) -> dict:
    """Raw active-alerts JSON for `area`. {'features': []} on any error."""
    try:
        return common.get_json(ALERTS_URL, {"area": area, "status": "actual",
                                            "message_type": "alert"}, ttl=ttl)
    except Exception:
        return {"features": []}
