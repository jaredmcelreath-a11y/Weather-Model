"""Print the live Ref/drift for a few screened cities. Run by hand.

Unit tests passed against both of the screen's original defects; only a live
pass caught them. Usage: python3 scripts/check_screen_drift.py
"""
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

import scan_cities            # noqa: E402
import screen_forecast        # noqa: E402
import screen_rules           # noqa: E402

CITIES = [("SFO", "KXHIGHTSFO", "high"), ("ATL", "KXLOWTATL", "low"),
          ("DEN", "KXHIGHDEN", "high"), ("NYC", "KXLOWTNYC", "low")]


def get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "screen-drift-check"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def main():
    now = datetime.now(timezone.utc)
    for city, series, variable in CITIES:
        point = scan_cities.point_for(series)
        resolved = scan_cities.resolve(*point, fetch=lambda u: get(u))
        tzname = resolved["timezone"]
        periods = get(resolved["forecast_hourly"])["properties"]["periods"]
        day = (now + timedelta(
            hours=screen_forecast.lst_offset_hours(tzname))).date()
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) \
            - timedelta(hours=screen_forecast.lst_offset_hours(tzname))
        station = scan_cities.station_for(resolved["stations_url"],
                                          fetch=lambda u: get(u))
        feats = get(f"https://api.weather.gov/stations/{station}/observations",
                    {"start": start.isoformat().replace("+00:00", "Z"),
                     "end": now.isoformat().replace("+00:00", "Z"),
                     "limit": 500})["features"]
        readings = []
        for f in feats:
            p = f["properties"]
            temp = screen_rules.c_to_f((p.get("temperature") or {}).get("value"))
            if temp is not None:
                readings.append((datetime.fromisoformat(p["timestamp"]), temp))
        realized = [t for _, t in readings]
        extreme = screen_forecast.daily_extremes(periods, day, tzname).get(variable)
        ref = screen_forecast.fold_realized(extreme, realized, variable)
        drift = screen_forecast.forecast_drift(periods, readings, now)
        implied = None if drift is None or extreme is None else \
            screen_forecast.fold_realized(extreme + drift, realized, variable)
        anchor, at = screen_forecast.observed_anchor(readings, now)
        print(f"{city:4} {variable:4} station={station:5} n_obs={len(readings):3} "
              f"anchor={anchor} at={None if at is None else at.strftime('%H:%MZ')} "
              f"fc_extreme={extreme} ref={ref} drift={drift} implied={implied}")


if __name__ == "__main__":
    main()
