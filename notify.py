"""Push notifications via ntfy (https://ntfy.sh).

The topic comes from the NTFY_TOPIC env var (a bare topic name or a full URL);
subscribe a phone to that topic in the ntfy app. Best-effort: a missing topic or
any network error is a silent no-op, so local runs without the secret don't fail.
"""

from __future__ import annotations

import os

import requests


def send_ntfy(title: str, message: str) -> bool:
    """POST `message` to the configured ntfy topic. Returns success."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    url = topic if topic.startswith("http") else f"https://ntfy.sh/{topic}"
    try:
        resp = requests.post(url, data=message.encode("utf-8"),
                             headers={"Title": title}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception:
        return False
