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
