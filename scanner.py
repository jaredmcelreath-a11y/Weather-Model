"""Read-only multi-city Kalshi price scanner.

Snapshots every city temperature bracket's price a few times a day and, after
close, records Kalshi's own settlement result. The pair answers one question:
are these markets' tails systematically overpriced, and in which cities?

Deliberately model-free. It reads no weather data and holds no per-city config —
city identity is the Kalshi series ticker, and the time axis is hours-to-close
from each market's own close_time. That is what makes ~20 cities cost nothing.

Read-only by construction: imports nothing from the trading modules and places
no orders.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import scan_log
from sources import kalshi

# Seconds between per-series requests. Kalshi rate-limits a full pass fired
# back-to-back: measured 2026-08-03, 51 series at full speed lost 21 of 26 to
# HTTP 429, while 0.5s spacing completed all 51 clean in ~31s. A snapshot runs
# three times a day, so half a minute costs nothing and losing two thirds of the
# cities costs the whole firing.
REQUEST_SPACING_S = 0.5


@dataclass
class Deps:
    """Injected IO, so a pass is testable without network or git."""
    list_series: Callable
    list_markets: Callable
    append_rows: Callable
    load_rows: Callable = lambda path: []
    sleep: Callable = time.sleep


def _real_deps() -> Deps:
    return Deps(
        list_series=kalshi.list_weather_series,
        list_markets=kalshi.list_series_markets,
        append_rows=lambda path, rows: scan_log.append_many(path, rows),
        load_rows=lambda path: scan_log.load(path),
    )


def snapshot_pass(now: datetime, deps: Deps) -> dict:
    """Price every bracket of every active city series, once.

    A failing series is counted and skipped rather than raised: one city's
    outage must not cost the other nineteen their snapshot."""
    rows, skipped, errors = [], 0, 0
    for s in deps.list_series():
        ticker = s["ticker"]
        try:
            # `status="open"` is essential, not an optimization: unfiltered, the
            # endpoint also returns every past day's settled markets. A live pass
            # without it captured 8,000 rows of which 7,520 had already closed
            # (2026-08-03), 13x the intended volume and all of it useless.
            markets = deps.list_markets(ticker, status="open")
        except Exception as e:            # noqa: BLE001 - see docstring
            print(f"[scan] {ticker}: markets unavailable ({e})")
            errors += 1
            markets = None
        # Pace AFTER every attempt, the failed ones included: a 429 is exactly
        # when backing off matters most.
        deps.sleep(REQUEST_SPACING_S)
        if markets is None:
            continue
        if not kalshi.is_series_active(markets, now):
            skipped += 1
            continue
        for m in markets:
            row = scan_log.build_snapshot_row(m, ticker, now)
            if row is not None:
                rows.append(row)
    written = deps.append_rows(scan_log.SNAPSHOT_PATH, rows)
    return {"series": len({r["series"] for r in rows}),
            "rows": written or 0, "skipped": skipped, "errors": errors}


def settlement_pass(now: datetime, deps: Deps) -> dict:
    """Record Kalshi's own outcome for every newly finalized bracket.

    Kalshi self-reports settlement (`result` + `status: finalized`), which is why
    the scanner needs no NWS CLI access and no per-city settlement basis. Tickers
    already on file are skipped, so the pass is safe to re-run."""
    known = {r.get("ticker") for r in deps.load_rows(scan_log.SETTLED_PATH)}
    rows, already, errors = [], 0, 0
    for s in deps.list_series():
        ticker = s["ticker"]
        try:
            markets = deps.list_markets(ticker, status="settled")
        except Exception as e:            # noqa: BLE001 - one city must not
            print(f"[scan] {ticker}: settled markets unavailable ({e})")
            errors += 1                   # cost the others their settlement
            markets = None
        deps.sleep(REQUEST_SPACING_S)     # see snapshot_pass
        if markets is None:
            continue
        for m in markets:
            row = scan_log.build_settlement_row(m, now)
            if row is None:
                continue
            if row["ticker"] in known:
                already += 1
                continue
            known.add(row["ticker"])
            rows.append(row)
    written = deps.append_rows(scan_log.SETTLED_PATH, rows)
    return {"settled": written or 0, "already": already, "errors": errors}


def main(argv: list, deps: Deps = None, now: datetime = None) -> int:
    """`python scanner.py snapshot` / `python scanner.py settle`.

    Returns an exit code so a bad invocation fails the Action loudly instead of
    silently recording nothing."""
    deps = deps or _real_deps()
    now = now or datetime.now(timezone.utc)
    command = argv[0] if argv else ""
    if command == "snapshot":
        print(f"[scan] snapshot {snapshot_pass(now, deps)}")
        return 0
    if command == "settle":
        print(f"[scan] settle {settlement_pass(now, deps)}")
        return 0
    if command == "report":
        import scan_report
        stats = scan_report.reliability(
            deps.load_rows(scan_log.SNAPSHOT_PATH),
            deps.load_rows(scan_log.SETTLED_PATH))
        print(scan_report.format_table(stats))
        return 0
    print("usage: scanner.py {snapshot|settle|report}")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
