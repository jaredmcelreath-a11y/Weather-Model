# KDFW Daily High/Low — Market Probability Model

Predicts the official daily **high** and **low** temperature at Dallas–Fort Worth
International Airport (**KDFW**) as a **probability for each market bin**, for use
with Robinhood/Kalshi midnight→midnight temperature contracts.

It blends many free forecast sources into a single honest distribution:

- **Open-Meteo Ensemble API** — ~120 ensemble members (GEFS, ECMWF-EPS, ICON-EPS,
  GEM); the backbone of the distribution.
- **Open-Meteo Forecast API** — deterministic GFS, ECMWF, ICON, GEM, and HRRR
  (high-res, best same-day).
- **NWS** — official forecast anchor and live KDFW observations.
- **IEM ASOS archive** — KDFW's actual history, for bias calibration + backtesting.
- **Kalshi public API** — live contract prices (the exchange behind Robinhood),
  series `KXHIGHTDAL` / `KXLOWTDAL`, shown next to the model with edge flags.

## How it works

1. **Sample** — each ensemble member contributes its daily max/min.
2. **Bias-correct** — subtract each source group's recent error vs. KDFW actuals.
3. **Spread** — pin the predictive width to the calibrated day-ahead error,
   then **shrink it as the day locks in** (an already-realized extreme collapses
   toward observation noise).
4. **Nowcast (today only)** — the observed max/min so far is a hard floor/ceiling.

Result: per-bin probabilities for today (updating live) and tomorrow (pure
forecast — best for the early-morning low you'd bet before bed).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Use

```bash
# Auto-refreshing dashboard (Today / Tomorrow selector, edge panel)
.venv/bin/streamlit run app.py

# One-off prediction in the terminal
.venv/bin/python -c "import calibration, model, datetime as d, json; \
print(json.dumps(model.predict(d.date.today(), calib=calibration.get()), indent=2))"

# Refresh bias/spread calibration (auto-refreshes daily anyway)
.venv/bin/python -c "import calibration; calibration.compute_and_save()"

# Backtest the pipeline against the last 60 days of actuals
.venv/bin/python backtest.py

# Unit tests
.venv/bin/python -m pytest tests/ -q
```

## Configuration

Edit `config.py` to set the market bin range (`BIN_LOW`/`BIN_HIGH`) to bracket
the contracts currently listed, and to adjust the model list or calibration window.

## Caveats

- **Live prices are Kalshi's** (the exchange behind Robinhood). Confirm they match
  your Robinhood screen. Kalshi's rules resolve on the **NWS Daily Climate Report**
  (CLIDFW), whereas Robinhood's page cites **Weather Underground** — usually the same
  whole-degree number, but they can differ at the margins.
- **Settlement window** is built to clock-time midnight→midnight (`America/Chicago`).
  The NWS *climate day* uses local **standard** time year-round, which can move a
  near-midnight low onto a different day during DST. Verify against one actually-settled
  KDFW market before trusting near-midnight edge cases.
- Backtest interval-coverage reads high partly because of coarse 1°F bins; trust
  **CRPS/Brier vs. the baseline** as the calibration signal.
- Not financial advice.
