# Deploying to the cloud (phone access + persistent accuracy)

Goal: view the dashboard on your phone with the data and **self-scoring history**
staying current even when your computer is off. Two free accounts: **GitHub** +
**Streamlit Community Cloud**. No database.

## How it works

- **Streamlit Cloud** runs the dashboard at a `…streamlit.app` URL. It sleeps when
  idle and wakes (with fresh data) whenever you open it. Live forecasts, picks,
  lock status, prices, and the **backtest** accuracy all work on every visit.
- **An hourly GitHub Action** (`.github/workflows/log.yml`) records a model
  snapshot to the forward log 24/7 — independent of any computer — and stores it
  on an orphan **`data`** branch (just the one file, so these commits don't restart
  the app).
- **`forecast_log.py`** reads that GitHub-hosted log on the cloud deploy (via the
  `[github]` secret), so **live self-scoring** and **per-lead sigma** persist across
  Streamlit Cloud's ephemeral restarts. Locally and inside the Action it just uses
  the local `forecast_log.jsonl` file.

## One-time setup

### 1. Push to a new **private** repo
```bash
gh repo create Weather-Model --private --source=. --push
```
If `gh` isn't authenticated, run `gh auth login` first. Without `gh`: create the
private repo on github.com, then
```bash
git remote add origin https://github.com/<you>/Weather-Model.git
git push -u origin main
```

### 2. Seed the `data` branch
In the repo: **Actions → "Log forecast snapshot" → Run workflow**. This first run
creates the `data` branch with `forecast_log.jsonl`; the hourly schedule takes over
after that. If the push step fails, set **Settings → Actions → General → Workflow
permissions → "Read and write permissions."**

### 3. Make a read token for the app
**GitHub Settings → Developer settings → Personal access tokens → Fine-grained
tokens → Generate:**
- Repository access: **Only select repositories → Weather-Model**
- Permissions: **Contents → Read-only**
- Copy the `github_pat_…` value.

### 4. Deploy on Streamlit Cloud
[share.streamlit.io](https://share.streamlit.io) → sign in with GitHub → **New
app** → repo, branch `main`, main file `app.py`. In **Advanced settings → Secrets**
paste:
```toml
[github]
repo = "<you>/Weather-Model"
ref = "data"
token = "github_pat_…"
```
Deploy, then open the URL on your phone (**Add to Home Screen**).

### 5. Screen cadence (external cron)

The Screen page's `Ref` column is recomputed only when `scan.yml` fires, so its
freshness *is* the firing cadence. GitHub's own scheduler will not carry it:
measured over 14.6h on 2026-08-04 it ran **9 of 15** scheduled hours (62%),
median 23 min late, worst gap 3 hours. Adding more `cron:` lines makes this
worse, not better — high-frequency schedules are the first GitHub drops.

**Nothing to set up — this is already wired.** `log.yml`'s external cron is the
one reliable clock in this repo (measured 2026-08-04: 100 runs, median gap
**10.0 min**, max 10.1), so its last step POSTs a `screen-run`
`repository_dispatch` whenever `minute % 30 < 10` — exactly one heartbeat per
half hour at any phase. The step runs only on that workflow's own
`repository_dispatch` runs: its in-repo schedule fallback fires on a *different*
10-minute phase, and on 2026-08-04 both landed inside one slot and ran the
screen twice 90 seconds apart. It reuses the `SCAN_GH_TOKEN` secret, because a PAT is
required here: events raised with the built-in `GITHUB_TOKEN` deliberately do
not start new workflow runs.

It is fire-and-forget (`continue-on-error`), so a failed dispatch can never fail
a logging run. If the screen goes quiet, check a recent "Log forecast snapshot"
run for `screen tick POST failed (HTTP …)` — 401/403 means the PAT expired,
and the screen falls back to `scan.yml`'s hourly schedule meanwhile.

**Optional standalone job.** To decouple the screen from the logger, create a
job on [cron-job.org](https://cron-job.org) (free) instead:

- **URL** `https://api.github.com/repos/<owner>/<repo>/dispatches`
- **Method** POST, **every 30 minutes**
- **Headers** `Accept: application/vnd.github+json`,
  `Authorization: Bearer <PAT with contents:write>`,
  `User-Agent: <owner>-screen-cron` (GitHub rejects requests without one)
- **Body** `{"event_type":"screen-run"}`

cron-job.org needs its own copy of the PAT, since a repo secret is not readable
from outside Actions. If you do this, **delete the "Tick the mispriced-bracket
screen" step from `log.yml`** — otherwise the screen fires twice per slot,
doubling the NWS load for no extra freshness.

Costs at 30 min: ~48 firings/day, ~3,840 NWS + ~1,970 Kalshi requests, ~56
Actions minutes (free — the repo is public), 48 commits and ~4.3 MB of PUTs to
`scan-data`. The in-repo hourly schedule stays on as a free fallback; a
duplicate firing is harmless, since the page reads only the newest one.

### 6. Screen row alert

A new same-day row on the Screen table pushes to ntfy within ~5 minutes.

**Nothing to set up beyond the secrets you already have** (`SCAN_GH_TOKEN` for
the scan-data branch, `NTFY_TOPIC` for the push). `log.yml` dispatches a
`screen-alert` `repository_dispatch` on every one of its 10-minute runs, and
`screen_alert.yml` checks twice per run, five minutes apart.

The alert re-uses `screen_reference.json`, published by every 30-minute screen
pass, rather than recomputing the NWS forecast — one check costs ~40 Kalshi and
~20 NWS requests. If the screen stalls for more than 90 minutes the forecast
half goes quiet and only `dead` rows alert; that appears in the job log as
`reference age …min — dead rows only`.

A ticker alerts once per climate day. State lives in `screen_alert_state.json`
on `scan-data` and is written only when something fires, so quiet checks cost
no commits.

To silence it entirely, disable the **Screen row alert** workflow in the Actions
tab — the dispatch step is `continue-on-error`, so nothing else is affected.

## Notes

- **Actions minutes:** private repos get 2000 free min/month; hourly runs use
  ~700–1400. For headroom, change `cron: "7 * * * *"` in the workflow to every 2–3
  hours.
- **First day is sparse:** live self-scoring only appears once logged days *settle*
  (a day's lead), so it fills in from the next day onward. Backtest accuracy shows
  immediately.
- **Rotating the token:** generate a new fine-grained token and update the
  `[github] token` secret in the Streamlit Cloud app settings.
- **Phone tab locked:** updates pause while locked/backgrounded and resume (with
  fresh data) when you reopen the tab — nothing is lost.
