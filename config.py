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

# NWS requires a descriptive User-Agent with contact info.
NWS_USER_AGENT = "kdfw-weather-model (jaredmcelreath@gmail.com)"

# --- Market bins ---
# Settlement rounds to a whole degree F, so each integer degree is its own bin:
# the bin labelled T captures the event round(daily_high) == T. The two tails
# capture "<= LOW" and ">= HIGH". Adjust LOW/HIGH to bracket the listed market.
BIN_LOW = 60    # lowest explicit integer-degree bin
BIN_HIGH = 110  # highest explicit integer-degree bin


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

# --- Radiational-cooling predictor (overnight low) ---
# On clear, calm nights the surface radiates heat away efficiently and the low
# undershoots what the models say. We flag a night as clear+calm when the mean
# overnight cloud cover and 10m wind are both below these thresholds, then apply
# a history-calibrated extra cooling offset to the low. Units match Open-Meteo:
# cloud cover in %, wind in km/h. Overnight window is [00:00, 08:00) local.
CLEAR_CLOUD_MAX = 30      # % mean cloud cover
CALM_WIND_MAX = 10        # km/h mean 10m wind
NIGHT_WINDOW_HOURS = (0, 8)

# --- Convective downside humility (daily low) ---
# Smooth gridded fields can't see a thunderstorm downdraft, so on a storm day the
# model locks to the morning low and over-reports confidence. When evening
# convection could still set a new lower minimum before midnight, we floor the
# low's 1-sigma spread at CONVECTIVE_SIGMA instead of collapsing to observation
# noise. Trigger fires on point POP/CAPE at KDFW OR an active severe-thunderstorm
# warning in the N/NW approach counties (storms move SE toward the metroplex; the
# airport sits on its north side). Widening is one-sided: the hard bound deletes
# all mass above the observed low.
CONVECTIVE_SIGMA = 3.0       # °F floor on today's low spread when storm risk is live
CONVECTIVE_POP_MIN = 30      # % precip probability (remaining hours) that arms the point trigger
CONVECTIVE_CAPE_MIN = 1000   # J/kg CAPE that arms the point trigger

# NWS county UGC codes for the N/NW storm approach to KDFW plus the metro counties
# themselves. A Severe Thunderstorm Warning intersecting this set arms the
# upstream trigger. (TXC + 3-digit county FIPS.)
CONVECTIVE_UPSTREAM_UGC = (
    "TXC497",  # Wise
    "TXC237",  # Jack
    "TXC367",  # Parker
    "TXC363",  # Palo Pinto
    "TXC503",  # Young
    "TXC121",  # Denton
    "TXC097",  # Cooke
    "TXC337",  # Montague
    "TXC439",  # Tarrant (airport county)
    "TXC113",  # Dallas
)

# Disk cache TTL (seconds) for live API calls, to avoid hammering on refresh.
CACHE_TTL_SECONDS = 600
