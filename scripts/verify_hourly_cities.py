"""Verify the Hourly page's city table against the live NWS API. Run by hand.

For each city: resolve its coordinate through api.weather.gov/points, assert the
first observation station and the timezone match the table, then fetch and parse
its CLI product. Unit tests cannot catch a wrong station id or a CLI location
that stops existing; only this can.

Usage: python3 scripts/verify_hourly_cities.py
"""
import json
import sys
import urllib.request
from datetime import datetime

sys.path.insert(0, ".")

import hourly_cities          # noqa: E402
from sources import nws_cli   # noqa: E402

HEADERS = {"User-Agent": "kdfw-weather-model (jaredmcelreath@gmail.com)"}


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    return json.load(urllib.request.urlopen(req, timeout=30))


def main():
    bad = 0
    for c in hourly_cities.CITIES:
        problems = []
        try:
            props = get(f"https://api.weather.gov/points/{c.lat},{c.lon}")["properties"]
            station = get(props["observationStations"])["features"][0][
                "properties"]["stationIdentifier"]
            if station != c.station:
                problems.append(f"station {station} != {c.station}")
            if props["timeZone"] != c.timezone:
                problems.append(f"tz {props['timeZone']} != {c.timezone}")
        except Exception as e:
            problems.append(f"points/stations failed: {e}")
        try:
            listing = get(nws_cli.list_url_for(c.cli_location))
            graph = listing.get("@graph") or []
            if not graph:
                problems.append("no CLI products")
            else:
                product = get(graph[0]["@id"])
                parsed = nws_cli.parse_cli(
                    product.get("productText") or "",
                    datetime.fromisoformat(product["issuanceTime"]))
                if not parsed:
                    problems.append("CLI product did not parse")
                else:
                    print(f"  {c.key:5} CLI {c.cli_location}: "
                          f"{parsed['high_f']}/{parsed['low_f']} "
                          f"({parsed['report_date']})")
        except Exception as e:
            problems.append(f"CLI failed: {e}")
        status = "OK" if not problems else "FAIL " + "; ".join(problems)
        print(f"{c.key:5} {c.station:5} {c.timezone:20} {status}")
        bad += bool(problems)
    print(f"\n{len(hourly_cities.CITIES) - bad}/{len(hourly_cities.CITIES)} verified")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
