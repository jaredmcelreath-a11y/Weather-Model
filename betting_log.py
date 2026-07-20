"""Betting-time forward log — a slot-keyed snapshot of the model + Kalshi market
at fixed afternoon clock times for the high (15:00-17:00 CDT) and sunrise-
anchored early-morning slots for the low (sunrise-90min to sunrise+30min), so the
model-vs-market edge and the settlement-gap predictor can be measured at the
moment bets are placed.

Separate from forecast_log.jsonl on purpose: forecast_log upserts on
(target_date, variable, lead_bucket) and would overwrite the same-day row every
run. This log keys on the capture slot, so each afternoon snapshot persists.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from config import TIMEZONE
from forecast_log import _load_github
from zoneinfo import ZoneInfo

import model
import settlement
import solar

TZ = ZoneInfo(TIMEZONE)
_PATH = os.path.join(os.path.dirname(__file__), "betting_log.jsonl")

# Each slot captures the variable it sits closest to settling. Afternoon slots
# capture the HIGH as it approaches its peak; the early-morning slots capture the
# LOW as it bottoms out near the sunrise trough. The opposite variable at each
# time is useless for edge measurement — an afternoon low is already settled, and
# a dawn high is a ~10h day-ahead forecast — so SLOT_VARS records only the one
# that matters.
#
# The morning low slots are SUNRISE-ANCHORED, not fixed clock times: the trough
# tracks sunrise (~6:25 CDT summer → ~7:30 CST winter local), so a fixed window
# would drift ~1h too early in winter. Each slot is an offset in minutes from that
# day's sunrise; `current_slot` resolves the offsets against solar.sunrise(today).
# The stored label is symbolic (e.g. "sr-30" = 30 min before sunrise) so a given
# solar-relative moment aggregates across days/seasons; the exact clock time stays
# recoverable from `captured_at`. Afternoon high slots stay fixed clock times —
# the mid-afternoon peak is season-stable enough not to need anchoring.
LOW_SLOT_OFFSETS = [("sr-90", -90), ("sr-60", -60), ("sr-30", -30),
                    ("sr", 0), ("sr+30", 30)]
HIGH_SLOTS = ["15:00", "15:30", "16:00", "16:30", "17:00"]
# Day-ahead probes. Day D's market opens 14:00Z on D−1, so tomorrow trades all
# evening; these ask whether a day-ahead entry carries more edge than the
# same-day slots. Fixed clock times — nothing solar about them.
EVENING_SLOTS = ["eve-21:00", "eve-22:00", "eve-23:00"]
# The last hour of a settlement day, anchored to the climate-day END (which is
# also the exact Kalshi close). In summer these land AFTER clock midnight
# (00:15/00:45 CDT) and target clock-yesterday; in winter they land before it
# (23:15/23:45 CST) and target clock-today. Anchoring to the boundary makes that
# seasonal shift automatic, the same trick the sunrise-anchored low slots use.
CLOSE_SLOT_OFFSETS = [("close-45", -45), ("close-15", -15)]
CLOSE_SLOTS = [lbl for lbl, _off in CLOSE_SLOT_OFFSETS]
SLOTS = ([lbl for lbl, _off in LOW_SLOT_OFFSETS] + HIGH_SLOTS
         + EVENING_SLOTS + CLOSE_SLOTS)
SLOT_VARS = {**{lbl: ("low",) for lbl, _off in LOW_SLOT_OFFSETS},
             **{s: ("high",) for s in HIGH_SLOTS},
             # Both variables: an evening capture is day-ahead for both, and at
             # the close both of the ending day's markets are still open.
             **{s: ("high", "low") for s in EVENING_SLOTS + CLOSE_SLOTS}}
# The scheduler fires on a ~10-min cadence (GitHub cron at :03/:13/../:53 + the
# external 10-min trigger). A 10-min cadence puts a run within 5 min of ANY
# clock minute, so a ±8-min window catches every slot regardless of the cron's
# phase — this holds for the fixed :00/:30 high slots and the arbitrary-minute
# sunrise-anchored low slots alike. The tighter cadence can now put two runs in
# one window; the per-slot upsert makes those redundant runs harmless.
SLOT_TOLERANCE_MIN = 8


def current_slot(now: datetime, tol_min=SLOT_TOLERANCE_MIN) -> str | None:
    """Slot label if `now` is within `tol_min` minutes of a slot (local time),
    else None. Morning low slots are resolved against today's sunrise; afternoon
    high slots are fixed clock times."""
    local = now.astimezone(TZ)
    sunrise = solar.sunrise(local.date())
    for label, off in LOW_SLOT_OFFSETS:
        slot_dt = sunrise + timedelta(minutes=off)
        if abs((local - slot_dt).total_seconds()) <= tol_min * 60:
            return label
    for s in HIGH_SLOTS:
        hh, mm = (int(x) for x in s.split(":"))
        slot_dt = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if abs((local - slot_dt).total_seconds()) <= tol_min * 60:
            return s
    for label in EVENING_SLOTS:
        hh, mm = (int(x) for x in label.split("-", 1)[1].split(":"))
        slot_dt = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if abs((local - slot_dt).total_seconds()) <= tol_min * 60:
            return label
    close_end = settlement.local_day_bounds(settlement.climate_day_of(local))[1]
    for label, off in CLOSE_SLOT_OFFSETS:
        slot_dt = close_end + timedelta(minutes=off)
        if abs((local - slot_dt).total_seconds()) <= tol_min * 60:
            return label
    return None


def slot_target_day(slot: str, now: datetime) -> date:
    """The date whose market `slot` captures.

    Existing same-day slots target the clock day (unchanged). Evening slots
    target tomorrow; close slots target the climate day that is ending — which
    is clock-yesterday in summer and clock-today in winter.
    """
    local = now.astimezone(TZ)
    if slot in EVENING_SLOTS:
        return local.date() + timedelta(days=1)
    if slot in CLOSE_SLOTS:
        return settlement.climate_day_of(local)
    return local.date()


def _parse(text: str) -> list[dict]:
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def _write(rows: list[dict], path: str) -> None:
    with open(path, "w") as fh:
        for rec in rows:
            fh.write(json.dumps(rec) + "\n")


def _github_cfg() -> dict | None:
    """Remote-log config from env, pointing at the betting-log file.

    Shares the repo/ref/token with forecast_log (set from Streamlit secrets);
    only the file path differs. Present on the cloud deploy, absent locally and
    in the scheduled Action — both of which work the local file directly.
    """
    repo = os.environ.get("FORECAST_LOG_GH_REPO")
    if not repo:
        return None
    return {
        "repo": repo,
        "ref": os.environ.get("FORECAST_LOG_GH_REF", "data"),
        "path": os.environ.get("FORECAST_LOG_GH_BETTING_PATH", "betting_log.jsonl"),
        "token": os.environ.get("FORECAST_LOG_GH_TOKEN") or None,
    }


def load(path: str | None = None) -> list[dict]:
    """All betting-time rows, oldest-written first.

    With no explicit path, transparently reads the GitHub-hosted file when the
    dashboard has configured one (cloud deploy); otherwise the local file. An
    explicit path always reads locally (used by record() and the Action).
    """
    if path is None:
        cfg = _github_cfg()
        if cfg:
            return _load_github(cfg)
    path = path or _PATH
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return _parse(fh.read())


def _key(rec: dict) -> tuple:
    return (rec["target_date"], rec["variable"], rec["capture_slot"])


def _top_bins(probabilities: dict, n: int = 5) -> list:
    items = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
    return [[label, round(p, 4)] for label, p in items[:n]]


def _row(day: str, variable: str, slot: str, cli_var: dict, hourly_var: dict,
         market_var: dict | None, flat_offset: float, captured: str,
         market_asks: list | None = None) -> dict:
    obs = cli_var.get("observed_so_far")
    cont = cli_var.get("observed_continuous")
    live_gap = (cont - obs) if (obs is not None and cont is not None) else None
    rec = {
        "target_date": day,
        "variable": variable,
        "capture_slot": slot,
        "captured_at": captured,
        "cli_consensus": cli_var.get("consensus"),
        "hourly_consensus": (hourly_var or {}).get("consensus"),
        "flat_offset": flat_offset,
        "live_gap": live_gap,
        "observed_so_far": obs,
        "observed_continuous": cont,
        "peak_locked": cli_var.get("peak_locked"),
        "sigma_used": cli_var.get("sigma_used"),
        "convective_widened": bool(cli_var.get("convective_widened")),
        "front_widened": bool(cli_var.get("front_widened")),
        "model_bins": _top_bins(cli_var.get("probabilities") or {}),
    }
    # Applied self-correction knobs baked into this row's consensus (only when
    # non-empty) — same disentanglement purpose as in forecast_log.
    corr = cli_var.get("corrections")
    if corr:
        rec["corrections"] = corr
    # Front-guard trigger details at betting time (only when fired) — the
    # margin recalibration's per-slot evidence.
    fg = cli_var.get("front_guard")
    if fg:
        rec["front_guard"] = fg
    # Raw per-contract quotes [floor, cap, yes_bid, yes_ask], close slots only.
    # The normalized `market_buckets` PMF has the overround removed and so cannot
    # answer "what would the settled bracket have COST" — the whole question the
    # last-hour trade turns on.
    if market_asks:
        rec["market_asks"] = market_asks
    if market_var:
        rec["market_ev"] = market_var.get("ev")
        rec["market_buckets"] = market_var.get("buckets")
        rec["market_volume"] = market_var.get("volume")
    return rec


def _snapshot_now(cli_snapshot: dict) -> datetime:
    """The snapshot's own capture instant, falling back to the wall clock."""
    stamp = cli_snapshot.get("updated")
    if stamp:
        try:
            return datetime.fromisoformat(stamp).astimezone(TZ)
        except ValueError:
            pass
    return datetime.now(TZ)


def _target_block(cli_snapshot: dict, slot: str, now: datetime):
    """(prediction block, block name) this slot captures, or (None, None).

    The block name doubles as the key into the market and hourly snapshots, which
    use the same today/tomorrow/yesterday naming. Same-day slots keep reading
    `today` unconditionally — byte-identical to the pre-slot-families behavior.
    """
    if slot in EVENING_SLOTS:
        return cli_snapshot.get("tomorrow"), "tomorrow"
    if slot in CLOSE_SLOTS:
        # Match by DAY rather than by name: the ending climate day lives in the
        # `yesterday` block in summer and the `today` block in winter.
        target = slot_target_day(slot, now).isoformat()
        for name in ("yesterday", "today"):
            block = cli_snapshot.get(name)
            if block and block.get("day") == target:
                return block, name
        return None, None
    return cli_snapshot.get("today"), "today"


def record(cli_snapshot: dict, hourly_snapshot: dict, slot: str, calib: dict,
           path: str | None = None, now: datetime | None = None) -> None:
    """Upsert the betting-time row(s) for `slot` — only the variable(s) that slot
    captures (see SLOT_VARS) on the day that slot targets (see slot_target_day)."""
    now = now or _snapshot_now(cli_snapshot)
    block, block_name = _target_block(cli_snapshot, slot, now)
    if not block or not block.get("day"):
        return
    day = block["day"]
    day_d = date.fromisoformat(day)
    captured = cli_snapshot.get("updated") or datetime.now(TZ).isoformat(timespec="seconds")
    market_block = (cli_snapshot.get("market") or {}).get(block_name, {})
    hourly_block = (hourly_snapshot or {}).get(block_name, {})
    asks = (cli_snapshot.get("market_asks") or {}) if slot in CLOSE_SLOTS else {}

    new_recs = []
    for variable in SLOT_VARS.get(slot, ("high", "low")):
        cli_var = block.get(variable)
        if not cli_var or not cli_var.get("probabilities"):
            continue
        flat_offset, _std = model._offset_bucket(
            calib.get("settlement_offset"), variable, day_d, calib)
        new_recs.append(_row(day, variable, slot, cli_var,
                             hourly_block.get(variable), market_block.get(variable),
                             flat_offset, captured, market_asks=asks.get(variable)))

    target = path or _PATH
    rows = load(target)
    index = {_key(r): i for i, r in enumerate(rows)}
    for rec in new_recs:
        k = _key(rec)
        if k in index:
            rows[index[k]] = rec
        else:
            index[k] = len(rows)
            rows.append(rec)
    _write(rows, target)


def capture_if_slot(cli_snapshot: dict, hourly_snapshot: dict, calib: dict,
                    now: datetime | None = None) -> str | None:
    """If `now` falls in a betting slot, record the snapshot and return the slot."""
    now = now or datetime.now(TZ)
    slot = current_slot(now)
    if slot is None:
        return None
    record(cli_snapshot, hourly_snapshot, slot, calib, now=now)
    return slot


def main() -> None:
    """Standalone capture (dry-run / manual). The scheduler uses capture_if_slot
    with the snapshot it already built."""
    import calibration
    from datetime import date
    from sources import kalshi
    calib = calibration.get(refresh=True)
    off = (calib or {}).get("settlement_offset")
    cli = model.snapshot(calib, settle_offset=off, continuous_obs=True)
    hourly = model.snapshot(calib)
    try:
        today = date.fromisoformat(cli["today"]["day"])
        tomorrow = date.fromisoformat(cli["tomorrow"]["day"])
        cli["market"] = kalshi.implied_block(today, tomorrow)
    except Exception as e:
        print(f"market block skipped: {e}")
    slot = capture_if_slot(cli, hourly, calib)
    print(f"betting capture: slot={slot}")


if __name__ == "__main__":
    main()
