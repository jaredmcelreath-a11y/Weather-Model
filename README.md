# Texas Daily High/Low — Market Probability Model

Predicts the official daily **high** and **low** temperature for two Texas cities —
**Dallas–Fort Worth International (KDFW)** and **Austin–Bergstrom (KAUS)** — as a
**probability for each market bin**, for use with Kalshi (and Robinhood/ForecastEx)
daily temperature contracts.

It blends many free forecast sources into a single honest distribution, shifts it
onto the exchange's actual settlement basis, and shows the live market next to the
model with edge flags. A companion set of pages tracks accuracy, bet history, and
an (optional, disabled-by-default) autonomous trader.

## Sources

- **Open-Meteo Ensemble API** — ~120 ensemble members (GEFS, ICON-EPS, ECMWF-EPS,
  GEPS); the backbone of the distribution.
- **Open-Meteo Forecast API** — deterministic GFS, ECMWF, ICON, GEM, and HRRR
  (high-res, best same-day).
- **NWS** — official forecast anchor, live observations, the CLIDFW/CLIAUS climate
  reports, and severe-weather alerts.
- **IEM ASOS/MOS archives** — each station's actual history, for bias calibration
  and backtesting.
- **Wunderground / TWC** — hourly forecast mirror (the Hourly page).
- **Kalshi public API** — live contract prices (series `KXHIGHTDAL`/`KXLOWTDAL` for
  Dallas, `KXHIGHAUS`/`KXLOWTAUS` for Austin), plus a read-only portfolio API for
  the History page and a write client for the trader.

## How it works

1. **Sample** — each ensemble member contributes its daily max/min.
2. **Bias-correct** — subtract each source group's recent error vs. the station's
   actuals (skill-weighted, group-rebalanced consensus).
3. **Spread** — pin the predictive width to the calibrated day-ahead error, then
   **shrink it as the day locks in** (a realized extreme collapses toward
   observation noise).
4. **Nowcast (today only)** — the observed max/min so far is a hard floor/ceiling,
   with lock guards for peak timing, dawn-low formation, and lone 5-min dips.
5. **Settlement basis** — shift onto the **NWS climate-day** the exchange settles
   on (local standard time year-round; ~+0.9°F hotter than the raw hourly max),
   floored/anchored by the official daily summary once it lands.

Result: per-bin probabilities for today (updating live) and tomorrow (pure
forecast — best for the early-morning low you'd bet before bed).

## Dashboard

Run with `streamlit run app.py`. A city toggle (Dallas / Austin / Both) sits on the
analytics pages, and `?city=Austin` deep-links. Nine pages:

- **Forecast** — the model vs. the live market, per-bin probabilities, edge flags,
  Kelly sizing, "Resolved %" lock indicator, and the morning recap.
- **Hourly** — Wunderground-style hourly forecast + nearby current temp.
- **Journal** — every settled day scored, with realized bet P&L.
- **History** — the Kalshi portfolio ("My Bets"): fills, positions, equity curve.
- **Trader** — control panel for the autonomous trading loop (ships disabled).
- **Edge** — model-vs-market edge and with/against-market P&L attribution.
- **Lab** — forward-logged model experiments and per-model head-to-head scores.
- **Accuracy** — backtest + live self-scoring, per-lead sigma, calibration drift.
- **Status** — log-derived health checks for both cities.

## Automation

- `.github/workflows/log.yml` — scheduled Action that logs forecasts/consensus
  24/7 and publishes `det_models.json` + the forward log to the `data` branch, so
  self-scoring persists across Streamlit Cloud's ephemeral restarts.
- `.github/workflows/trade.yml` — the autonomous-trader cron (agreement-based
  entry, ask-referenced stops; **ships disabled** behind a kill switch + shadow
  mode, writing to a dedicated `trade-data` branch).
- **Alerts** — ntfy push notifications: Resolved-% threshold, CLI report issued,
  Storm Watch / Front Risk, and a 6:30am morning recap (`alerts.py`).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Use

```bash
# Dashboard (city toggle, edge panel, all nine pages)
.venv/bin/streamlit run app.py

# One-off prediction in the terminal
.venv/bin/python -c "import calibration, model, datetime as d, json; \
print(json.dumps(model.predict(d.date.today(), calib=calibration.get()), indent=2))"

# Refresh bias/spread calibration (auto-refreshes daily anyway)
.venv/bin/python -c "import calibration; calibration.compute_and_save()"

# Backtest the pipeline against recent actuals
.venv/bin/python backtest.py

# Unit tests
.venv/bin/python -m pytest tests/ -q
```

## Configuration

`config.py` holds every station- and market-specific constant: the `STATIONS`
table (add a city there), the market bin range (`BIN_LOW`/`BIN_HIGH`), the
Open-Meteo model lists, and calibration/lead-time settings. Cloud secrets
(`[github]`, `[kalshi]`, `[open_meteo]`, `[trade]`) are documented in `DEPLOY.md`.

## Caveats

- **Live prices are Kalshi's.** Confirm they match your Robinhood screen. Kalshi
  settles on the **NWS Daily Climate Report** (CLIDFW/CLIAUS); Robinhood's page
  cites **Weather Underground** — usually the same whole-degree number, but they
  can differ at the margins.
- **Settlement day** is the NWS climate day: midnight→midnight *local standard
  time* year-round (1 AM→1 AM CDT during DST), distinct from the wall-clock zone.
  Verify near-midnight edge cases against one actually-settled market.
- The pre-preliminary live feed is whole-°C, so the model **cannot** resolve
  e.g. 100 vs. 101°F before the 4:40 PM prelim — that wall lifts for everyone at
  once. Backtests confirm no live sub-°C edge exists to exploit.
- Backtest interval-coverage reads high partly because of coarse 1°F bins; trust
  **CRPS/Brier vs. the baseline** as the calibration signal.
- Not financial advice.
