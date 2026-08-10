"""Build ONE consensus document against live data, writing nothing. By hand.

Prints the reference's age, how many cities were built, and a sample city, so a
change can be checked against the real 20-city fetch before it reaches the
schedule. Neither the document nor the log is written.

Needs SCAN_GH_REPO/SCAN_GH_BRANCH in the environment to read the scan-data
branch (public: no token required for reads).

Usage: SCAN_GH_REPO=owner/repo python3 scripts/check_city_consensus.py
"""
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

import city_consensus          # noqa: E402


def main():
    now = datetime.now(timezone.utc)
    real = city_consensus._real_deps()
    deps = city_consensus.Deps(
        read_reference=real.read_reference,
        fetch=real.fetch,
        write_doc=lambda path, obj: print(f"[stubbed] would write {path}"),
        append_rows=lambda path, rows: print(
            f"[stubbed] would append {len(rows)} rows to {path}") or len(rows),
    )
    result = city_consensus.run(now, deps)
    print(f"result: {result}")
    if not result["cities"]:
        return 1

    reference = real.read_reference()
    cities = city_consensus.cities_from_reference(reference)
    raw = real.fetch([(c["lat"], c["lon"]) for c in cities])
    doc = city_consensus.build(reference, raw, cities, now)
    sample = sorted(doc["cities"])[0]
    print(f"\n--- {sample} ---")
    print(json.dumps(doc["cities"][sample], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
