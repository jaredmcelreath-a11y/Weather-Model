"""Run ONE screen-alert check against live data with the push stubbed. By hand.

Prints the reference's age, what the check found, and the exact notification it
would have sent. State is read but never written, so running this cannot
suppress a real alert later.

Needs SCAN_GH_REPO/SCAN_GH_BRANCH/SCAN_GH_TOKEN in the environment to read the
scan-data branch. NTFY_TOPIC is deliberately NOT used — nothing is sent.

Usage: SCAN_GH_REPO=owner/repo SCAN_GH_TOKEN=… python3 scripts/check_screen_alert.py
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

import screen_alert          # noqa: E402


def main():
    now = datetime.now(timezone.utc)
    real = screen_alert._real_deps()
    reference = real.read_reference()
    age = screen_alert.reference_age_minutes(reference, now)
    cities = len((reference or {}).get("cities") or {})
    print(f"reference: {cities} cities, age "
          f"{'unknown' if age is None else round(age, 1)} min, "
          f"forecast screen "
          f"{'ON' if screen_alert.forecast_is_usable(reference, now) else 'OFF'}")
    if not cities:
        print("no reference on the data branch yet — run screen.py first")
        return 1

    sent = []
    deps = screen_alert.Deps(
        read_reference=real.read_reference,
        read_state=real.read_state,
        write_state=lambda obj: print(f"[stubbed] would write {len(obj)} day(s) of state"),
        list_markets=real.list_markets,
        fetch_obs=real.fetch_obs,
        notify=lambda title, body: sent.append((title, body)) or True,
    )
    print(f"result: {screen_alert.check(now, deps)}")
    for title, body in sent:
        print(f"\n--- would push ---\n{title}\n{body}")
    if not sent:
        print("\nnothing new this check (expected most of the time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
