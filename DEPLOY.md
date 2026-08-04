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

So drive it externally, the same way `log.yml` gets its dependable 10-min
cadence. On [cron-job.org](https://cron-job.org) (free), create a job:

- **URL** `https://api.github.com/repos/<owner>/<repo>/dispatches`
- **Method** POST, **every 30 minutes**
- **Headers** `Accept: application/vnd.github+json`,
  `Authorization: Bearer <PAT with contents:write>`,
  `User-Agent: <owner>-screen-cron` (GitHub rejects requests without one)
- **Body** `{"event_type":"screen-run"}`

The PAT can be the same one behind `SCAN_GH_TOKEN`; cron-job.org holds its own
copy, since a repo secret is not readable from outside Actions. Verify with one
manual run of the job, then check the Actions tab for a `repository_dispatch`
run of "Kalshi multi-city price scan".

Costs at 30 min: ~48 firings/day, ~3,840 NWS + ~1,970 Kalshi requests, ~56
Actions minutes (free — the repo is public), 48 commits and ~4.3 MB of PUTs to
`scan-data`. The in-repo hourly schedule stays on as a free fallback; a
duplicate firing is harmless, since the page reads only the newest one.

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
