"""Verify each city's station against how Kalshi ACTUALLY SETTLED.

Runs daily from scan.yml beside the settlement step, and by hand whenever a city
is added or a mapping is doubted. It was manual-only until 2026-08-17, and that
was the whole defect: Chicago and Houston were pointed at the wrong airport for
twelve days, not because the check could not find them -- this script found both
the first time it was run -- but because nothing ever ran it.

`verify_hourly_cities.py` checks geography: that a coordinate resolves to the
station the table claims. It cannot catch the failure that matters here --- a
coordinate that resolves perfectly to the wrong airport. Chicago sat on O'Hare
and Houston on Intercontinental for months, both geographically correct and both
settling elsewhere, because the city's busiest airport is not always the one
Kalshi uses.

This script tests outcomes instead. For every candidate station of every city it
pulls the NWS CLI daily product, reads the MAXIMUM and MINIMUM, and asks how
often that value falls inside the bracket Kalshi settled YES. The real station
matches essentially every day; a wrong one matches around half, because adjacent
airports agree whenever the bracket is wide enough to cover both.

Note this scores against the CLI basis, which is what Kalshi settled on before
the ~2026-08-14 move to The Weather Company. It stays valid as a station test:
the settlement SOURCE changed, the station did not.

Usage: python3 scripts/verify_city_stations.py [--days 35] [--notify]
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, ".")

import hourly_cities   # noqa: E402
import scan_cities     # noqa: E402

HEADERS = {"User-Agent": "kdfw-weather-model (jaredmcelreath@gmail.com)"}

# Seconds between Kalshi requests. Forty series x two statuses is eighty calls,
# and the scanner measured 51 unpaced series losing 21 of 26 to HTTP 429
# (scanner.REQUEST_SPACING_S documents it). The single retry below does not
# survive a sustained 429, and a rate-limited run does not fail loudly -- it
# reports "UNVERIFIED" for real cities, which reads as a data hiccup and gets
# ignored. That is precisely how a wrong station survives an audit. Mattered
# little while this was run by hand; it is now on a daily schedule.
REQUEST_SPACING_S = 0.5

# Candidate stations per city: the one we use plus every plausible rival. A city
# with one obvious airport still gets scored -- a lone candidate matching 6/33
# would mean the mapping is wrong in a way this list is too narrow to name.
CANDIDATES = {
    "ATL": ["ATL", "PDK"], "AUS": ["AUS"], "BOS": ["BOS"],
    "CHI": ["MDW", "ORD"], "DAL": ["DFW", "DAL"],
    "DC": ["DCA", "IAD", "BWI"], "DEN": ["DEN", "APA"],
    "HOU": ["HOU", "IAH"], "LAX": ["LAX"], "LV": ["LAS"], "MIA": ["MIA"],
    "MIN": ["MSP"], "NOLA": ["MSY", "NEW"],
    "NYC": ["NYC", "JFK", "LGA", "EWR"], "OKC": ["OKC", "PWA"],
    "PHIL": ["PHL"], "PHX": ["PHX", "DVT"], "SATX": ["SAT", "SSF"],
    "SEA": ["SEA", "BFI"], "SFO": ["SFO", "OAK"],
}

# A station is only endorsed if it clears this. The gap between right and wrong
# is enormous (33/33 vs 6/33 for Houston), so the threshold is deliberately
# blunt -- anything near it means the data, not the mapping, needs a look.
PASS_RATE = 0.90

# Fetches that failed after a retry. Collected rather than raised so one dead
# series does not hide the other nineteen cities' results, but reported at the
# end and counted as a failure -- an unscored city is an unverified city.
FETCH_ERRORS = []


def cli_reports(pil: str, days: int) -> dict:
    """{date: (max, min)} from the NWS CLI archive for one station."""
    end = date.today()
    start = end - timedelta(days=days)
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
           f"?pil=CLI{pil}&sdate={start:%Y-%m-%d}&edate={end:%Y-%m-%d}"
           "&fmt=text&limit=400")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        text = urllib.request.urlopen(req, timeout=120).read().decode("latin-1")
    except Exception as exc:                      # noqa: BLE001 - report, skip
        print(f"    ! CLI{pil} fetch failed: {exc}")
        return {}
    out = {}
    for chunk in re.split(r"\n(?=\d{3,4}\s*\nCDUS)", text):
        stamp = re.search(r"CLIMATE SUMMARY FOR (\w+ +\d+ +\d{4})", chunk)
        high = re.search(r"^ *MAXIMUM +(-?\d+)", chunk, re.M)
        low = re.search(r"^ *MINIMUM +(-?\d+)", chunk, re.M)
        if not (stamp and high):
            continue
        try:
            day = datetime.strptime(
                re.sub(r"\s+", " ", stamp.group(1)).strip(), "%B %d %Y").date()
        except ValueError:
            continue
        # Each day gets a 4pm preliminary and a final; the final wins.
        preliminary = "VALID TODAY AS OF" in chunk
        if day not in out or (out[day][2] and not preliminary):
            out[day] = (int(high.group(1)),
                        int(low.group(1)) if low else None, preliminary)
    return out


def settled_targets() -> dict:
    """{city: [(day, index, (lo, hi))]} for every YES-settled Kalshi bracket.

    `index` is 0 for a high series and 1 for a low, matching `cli_reports`.
    """
    out = defaultdict(list)
    for series, city in scan_cities._SERIES_CITY.items():
        markets = []
        for status in ("settled", "closed"):
            url = ("https://api.elections.kalshi.com/trade-api/v2/markets"
                   f"?series_ticker={series}&status={status}&limit=200")
            # Retry once: forty rapid calls draw the occasional reset, and a
            # city that silently scores zero days looks identical to a city that
            # passes. Losing Houston to a dropped connection is exactly how the
            # wrong station survives this check.
            for attempt in (1, 2):
                try:
                    markets += json.load(
                        urllib.request.urlopen(url, timeout=45))["markets"]
                    break
                except Exception as exc:          # noqa: BLE001 - report below
                    if attempt == 2:
                        FETCH_ERRORS.append(f"{series} ({status}): {exc}")
                    else:
                        time.sleep(2)
            time.sleep(REQUEST_SPACING_S)
        for market in markets:
            if market.get("result") != "yes":
                continue
            target = _target(market.get("title") or "")
            stamp = re.search(r"-(\d{2}[A-Z]{3}\d{2})-", market["ticker"])
            if not (target and stamp):
                continue
            day = datetime.strptime(stamp.group(1), "%y%b%d").date()
            out[city].append((day, 0 if "HIGH" in series else 1, target))
    return out


def _target(title: str):
    """The (lo, hi) degree range a settled title asserts, or None.

    Kalshi words a bracket three ways -- 'be 86-87', 'be >71', 'be <64' -- and an
    unbounded side is represented by a sentinel rather than None so the caller
    has one comparison to make, not three."""
    for pattern, build in (
            (r"be (\d+)-(\d+)", lambda g: (int(g[0]), int(g[1]))),
            (r"be >(\d+)", lambda g: (int(g[0]) + 1, 999)),
            (r"be <(\d+)", lambda g: (-999, int(g[0]) - 1))):
        found = re.search(pattern, title)
        if found:
            return build(found.groups())
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=35,
                    help="how far back to pull CLI products (default 35)")
    ap.add_argument("--notify", action="store_true",
                    help="push the failing lines to ntfy (for the daily run)")
    args = ap.parse_args()

    in_use = {c.key: c.station for c in hourly_cities.CITIES}
    targets = settled_targets()
    bad = 0
    # What a push would say. Collected rather than re-derived at the end: the
    # message must name the same lines the log does, and two renderings of one
    # result eventually disagree about which city was wrong.
    problems = []

    for city in sorted(CANDIDATES):
        scores = []
        for pil in CANDIDATES[city]:
            reports = cli_reports(pil, args.days)
            hits = total = 0
            for day, index, (lo, hi) in targets.get(city, []):
                report = reports.get(day)
                if not report or report[index] is None:
                    continue
                total += 1
                hits += lo <= report[index] <= hi
            if total:
                scores.append((hits / total, hits, total, pil))
        if not scores:
            bad += 1
            line = f"{city:<5} UNVERIFIED: no settled days scored"
            problems.append(line)
            print(line)
            continue
        scores.sort(reverse=True)
        rate, hits, total, best = scores[0]
        detail = "  ".join(f"CLI{p}={h}/{t}" for _, h, t, p in scores)
        expected = in_use.get(city)
        if f"K{best}" != expected or rate < PASS_RATE:
            bad += 1
            line = (f"{city:<5} WRONG: using {expected}, data says K{best}"
                    f" ({rate:.0%})   {detail}")
            problems.append(line)
            print(line)
        else:
            print(f"{city:<5} ok {expected} {hits}/{total}   {detail}")

    # Pushed here rather than from the workflow so the YAML stays a one-liner
    # and there is exactly one place that decides what "failed" means. Off by
    # default: running this by hand must never page anybody.
    if args.notify and (problems or FETCH_ERRORS):
        import notify                              # noqa: E402 - optional path
        body = "\n".join(problems + [f"fetch failed: {e}" for e in FETCH_ERRORS])
        notify.send_ntfy(f"Station audit: {len(problems)} mismatch(es)", body)

    if FETCH_ERRORS:
        print(f"\n{len(FETCH_ERRORS)} fetch(es) failed after a retry:")
        for line in FETCH_ERRORS:
            print(f"  {line}")

    print(f"\n{bad} mismatch(es)")
    return 1 if (bad or FETCH_ERRORS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
