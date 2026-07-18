"""Station constants, market bin definitions, and lead-time settings for the
KDFW high/low probability model.

Everything that is station- or market-specific lives here so the rest of the
code stays generic. Edit the bin range to match the contracts Robinhood/Kalshi
is currently listing for Dallas.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

# --- Station: Dallas–Fort Worth International Airport (KDFW) ---
STATION_ID = "KDFW"
LAT = 32.90
LON = -97.04
TIMEZONE = "America/Chicago"  # all daily-window math happens in this tz
# The NWS Climatological Report (CLIDFW) — what Kalshi settles on — defines its
# climate day as midnight-to-midnight LOCAL STANDARD TIME (UTC−6) year-round,
# i.e. 1:00 AM → 1:00 AM CDT during daylight saving. This is the settlement-day
# boundary, distinct from TIMEZONE (America/Chicago), which stays the
# wall-clock/diurnal zone for hour-of-day logic and all display. Verified
# 2026-07-14 (docs/benchmarks/2026-07-14/climate-day/FINDINGS.md).
CLIMATE_TZ = "Etc/GMT+6"

# NWS requires a descriptive User-Agent with contact info.
NWS_USER_AGENT = "kdfw-weather-model (jaredmcelreath@gmail.com)"

# --- Market bins ---
# Settlement rounds to a whole degree F, so each integer degree is its own bin:
# the bin labelled T captures the event round(daily_high) == T. The two tails
# capture "<= LOW" and ">= HIGH".
#
# The range brackets DFW's CLIMATE, not just the currently listed market. The
# tails are open-ended: a query that needs to resolve INSIDE one can't be
# answered and the model abstains (model.prob_at_most / prob_at_least), so a
# range that real weather reaches would cost live pricing. DFW's all-time
# records are about -8F and 113F; 11 years of dailies (2015-2025) span -2F to
# 110F. -10..115 clears both with margin, so the tails stay negligible.
BIN_LOW = -10   # lowest explicit integer-degree bin
BIN_HIGH = 115  # highest explicit integer-degree bin

# Market-implied forecast: drop buckets whose mid YES price is below this floor
# before normalizing the PMF/EV. Far-out contracts often sit at 1-2c of bid/ask
# noise; on a locked day that noise drags the market's implied EV off the settled
# bucket, unfairly inflating its measured error vs the model. Guarded so a flat/
# illiquid market (every bucket below the floor) is kept whole rather than emptied.
MARKET_MIN_BUCKET_PRICE = 0.03  # 3c

# ⚠-marker threshold on the Edge page: a (slot, variable) subset whose median
# traded market volume falls below this is flagged as a thin market, so a
# "market win/loss" that rode on almost no trading is visible. Annotation only —
# nothing is excluded from the tally. Conservative first guess; retune with data.
MARKET_LIQUIDITY_FLOOR = 20   # contracts


def bin_labels() -> list[str]:
    """Ordered bin labels including the open-ended tails."""
    labels = [f"<= {BIN_LOW}"]
    labels += [str(t) for t in range(BIN_LOW + 1, BIN_HIGH)]
    labels += [f">= {BIN_HIGH}"]
    return labels


# --- Forecast models pulled from Open-Meteo ---
# Deterministic models for the Forecast API (HRRR is high-res, best same-day).
DETERMINISTIC_MODELS = [
    "gfs_seamless",
    "ecmwf_ifs025",
    "icon_seamless",
    "gem_seamless",
    "gfs_hrrr",
]
# Ensemble systems for the Ensemble API (each expands into ~20-50 members).
ENSEMBLE_MODELS = [
    "gfs_seamless",      # GEFS, ~31 members
    "icon_seamless",     # ICON-EPS, ~40 members
    "ecmwf_ifs025",      # ECMWF-EPS, ~50 members
    "gem_global_ensemble",  # GEPS, ~20 members (gem_seamless returns only the
                            # control series — this id delivers the real members)
]

# --- Candidate model sets (shadow consensus) ---
# Superset of the production lists used ONLY by the shadow/candidate consensus
# (see model.snapshot(include_candidate=True)). The production consensus never
# reads these. Promotion = move a model from here into the production list above.
# Contents fixed by the 2026-07-18 probe (docs/benchmarks/2026-07-18-model-
# diversity/probe_results.md): GraphCast + BOM ensemble returned all-null
# temperature at KDFW and were dropped. jma_seamless was then dropped too — the
# do-no-harm assessment measured it at 4.11°F high MAE at KDFW (unusable), so it
# adds no signal to the shadow comparison.
CANDIDATE_DETERMINISTIC_MODELS = DETERMINISTIC_MODELS + [
    "ecmwf_aifs025_single",  # ECMWF AIFS (AI)
    "ukmo_seamless",         # UK Met Office
    "meteofrance_seamless",  # Meteo-France ARPEGE/AROME
]
CANDIDATE_ENSEMBLE_MODELS = ENSEMBLE_MODELS + [
    "ukmo_global_ensemble_20km",  # UKMO EPS, ~18 members
]

# --- Lead-time buckets for bias / spread calibration ---
# A forecast's error grows with how far ahead the valid day is. We bucket by the
# target day's calendar offset from "now": today (the extreme is same-day, often
# partly observed) vs tomorrow (~24h out) vs the day after (~36h out). The hour
# values are the nominal lead each bucket represents.
LEAD_BUCKETS_HOURS = [0, 24, 36]


def lead_bucket(now: datetime, day: date) -> int:
    """Lead bucket (one of LEAD_BUCKETS_HOURS) for predicting `day` as of `now`.

    Keyed on calendar-day offset, which is the meaningful signal here: an
    evening prediction of tomorrow's pre-dawn low is still a ~day-ahead forecast
    even though the clock distance to midnight is small. Today -> 0, tomorrow ->
    24, anything further -> 36.
    """
    delta = (day - now.astimezone(ZoneInfo(TIMEZONE)).date()).days
    if delta <= 0:
        return 0
    if delta == 1:
        return 24
    return 36


# Interim 1-sigma inflation per lead bucket, used for the pure-forecast spread
# until the forward prediction log has enough settled days to derive empirical
# per-lead sigma (calibration writes sigma.by_lead, which then takes precedence).
# Tomorrow's day-ahead error is meaningfully wider than the short-lead archive
# sigma calibration measures, so we widen it.
LEAD_SIGMA_INFLATION = {0: 1.0, 24: 1.5, 36: 1.8}

# How many recent days to use when estimating bias / spread inflation.
CALIBRATION_WINDOW_DAYS = 45

# --- Same-day "extreme locked" detector ---
# Once today's observed temperature has retreated this many °F from the running
# max (high) or risen this much above the running min (low), treat the day's
# extreme as already set: the realized extreme is the answer and the forecast's
# projected further rise/fall is noise, so collapse the samples to observed.
# Conservative by design (a clear descent), so a brief dip before a higher peak
# won't false-lock. ~2°F clears observation/quantization noise comfortably.
PEAK_LOCK_DROP = 2.0

# The low locks early once past sunrise and risen this many °F above the running
# min — the dawn minimum is behind us; the margin clears obs/rounding jitter.
LOW_LOCK_RISE = 0.8

# Symmetric early lock for the high: the afternoon maximum forms a few hours after
# solar noon, so once we're past (solar noon + HIGH_LOCK_NOON_OFFSET_HOURS) and the
# temp has eased HIGH_LOCK_DROP °F off a real (post-trough) peak, the high is in —
# collapse the spread without waiting for the full PEAK_LOCK_DROP retreat. Gating on
# solar noon (not a fixed clock hour) tracks the peak across seasons: at KDFW the
# offset lands ~16:46 CDT in July and ~15:46 CST in January. The high needs no
# convective-downside guard the way the low does; nothing sets a new daytime max
# after the afternoon window.
HIGH_LOCK_NOON_OFFSET_HOURS = 3.25   # hours after solar noon the peak is treated as in
HIGH_LOCK_DROP = 0.8                  # °F off the running max, past the gate, to early-lock

# Plateau lock: past the gate, a high that has stopped climbing (holds within this
# many °F of its running max without a new high — essentially flat, clearing only
# rounding jitter) is treated as in, so a flat-topped peak locks while the market's
# still live instead of waiting for the temp to fall. Kept below HIGH_LOCK_DROP so a
# genuine small ease (not a flat hold) still routes through the early-lock instead.
HIGH_PLATEAU_MAX = 0.3
# Persistence guard: only on a *bumpy* afternoon (recent sub-hourly readings jitter
# more than this std, °F) does the blunt PEAK_LOCK_DROP lock need a second confirming
# reading — so a lone convective dip before a higher peak can't false-lock. Calm days
# lock on the first reading exactly as before (no delay).
HIGH_BUMPY_STD = 1.5

# --- Front-aware locked low ---
# A locked morning min can still be undercut by a real evening cold front
# before midnight (Kalshi settles the full-day min), and the POP-gated
# convective floor can't see a dry front. A member whose obs-anchored
# afternoon/evening projection undercuts the observed min by at least the
# margin reports that projection instead of the observed min. The margin
# clears anchor jitter; scanning only hours >= FRONT_SCAN_FROM_HOUR keeps the
# dawn-adjacent wobble (the reason the early sunrise lock exists) from
# reopening a calm day's lock — "a new low later today" is an
# afternoon/evening event.
FRONT_UNDERCUT_MARGIN = 0.5   # °F below the observed min a projection must reach
FRONT_SCAN_FROM_HOUR = 12     # local hour the undercut scan starts
FRONT_SIGMA_MIN = 1.5   # °F; sigma floor while the front guard holds a locked
                        # low open. A projected-but-unrealized evening event
                        # deserves at least this much spread (the same idiom and
                        # value as CONVECTIVE_SIGMA_MIN) — even when every member
                        # agrees on the undercut and the raw sample spread
                        # collapses, the projection is still hours ahead.

MAX_CLI_GAP = 3.0   # °F; largest CLI-vs-hourly low gap we trust as a live anchor (spike clamp)

# --- Radiational-cooling predictor (overnight low) ---
# On clear, calm nights the surface radiates heat away efficiently and the low
# undershoots what the models say. We flag a night as clear+calm when the mean
# overnight cloud cover and 10m wind are both below these thresholds, then apply
# a history-calibrated extra cooling offset to the low. Units match Open-Meteo:
# cloud cover in %, wind in km/h. Overnight window is [00:00, 08:00) local.
CLEAR_CLOUD_MAX = 30      # % mean cloud cover
CALM_WIND_MAX = 10        # km/h mean 10m wind
NIGHT_WINDOW_HOURS = (0, 8)

# Forecast low (°F) at/above which the warm-night low-bias correction applies.
# On warm nights the consensus low runs cold in a way the flat bias misses.
WARM_LOW_THRESHOLD = 76

# --- Convective downside humility (daily low) ---
# Smooth gridded fields can't see a thunderstorm downdraft, so on a storm day the
# model locks to the morning low and over-reports confidence. When evening
# convection could still set a new lower minimum before midnight, we floor the
# low's 1-sigma spread instead of collapsing to observation noise. Widening is
# one-sided: the hard bound deletes all mass above the observed low.
#
# The point trigger is precip probability (POP), NOT CAPE. CAPE measures latent
# instability that runs high on storm-free Texas summer afternoons, so arming on
# it spread the locked low downward almost every hot day; POP is the model's
# actual expectation that storms fire. The downside scales with POP: a barely-
# armed day earns CONVECTIVE_SIGMA_MIN, a near-certain-storm day the full
# CONVECTIVE_SIGMA. An active severe-thunderstorm warning in the N/NW/SW approach
# counties (NW storms move SE toward the metroplex; SW/W storms track NE toward
# and over it; the airport sits between Dallas and Fort Worth) is direct evidence
# of storms and commands the full floor on its own.
CONVECTIVE_SIGMA = 3.0       # °F max downside floor (near-certain evening storms / upstream warning)
CONVECTIVE_SIGMA_MIN = 1.5   # °F downside floor the moment POP clears the arming threshold
CONVECTIVE_POP_MIN = 30      # % precip probability (remaining hours) that arms the trigger
CONVECTIVE_POP_FULL = 70     # % POP at/above which the full CONVECTIVE_SIGMA applies

# NWS county UGC codes for the N/NW and SW/W storm approaches to KDFW plus the
# metro counties themselves, each mapped to (county name, approach direction). A
# Severe Thunderstorm Warning intersecting this set arms the upstream trigger; the
# name/direction feed the storm-watch panel. (TXC + 3-digit county FIPS.) This dict
# is the single source of truth — CONVECTIVE_UPSTREAM_UGC is derived from its keys.
CONVECTIVE_UPSTREAM_COUNTIES = {
    # N/NW approach (storms move SE toward the metroplex)
    "TXC497": ("Wise", "NW"),
    "TXC237": ("Jack", "NW"),
    "TXC367": ("Parker", "W"),
    "TXC363": ("Palo Pinto", "W"),
    "TXC503": ("Young", "NW"),
    "TXC121": ("Denton", "N"),
    "TXC097": ("Cooke", "N"),
    "TXC337": ("Montague", "NW"),
    # SW/W/S approach (storms track NE toward and over the metroplex)
    "TXC251": ("Johnson", "SW"),
    "TXC221": ("Hood", "SW"),
    "TXC425": ("Somervell", "SW"),
    "TXC143": ("Erath", "SW"),
    "TXC139": ("Ellis", "S"),
    # metro counties (the airport itself)
    "TXC439": ("Tarrant", "metro"),
    "TXC113": ("Dallas", "metro"),
}
CONVECTIVE_UPSTREAM_UGC = tuple(CONVECTIVE_UPSTREAM_COUNTIES)

# Disk cache TTL (seconds) for live API calls, to avoid hammering on refresh.
CACHE_TTL_SECONDS = 600
