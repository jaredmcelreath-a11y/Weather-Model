---
name: verify
description: Run the Streamlit dashboard locally on this Mac and screenshot any page headlessly to verify UI changes against real data
---

# Verify — run the dashboard locally and eyeball a page

Works on this Mac as of 2026-07-19 (streamlit 1.50.0, streamlit_autorefresh,
cryptography 38.0.4, websocket-client all installed via `pip install --user`;
`cryptography` must stay pinned ≤38.x — newer needs a Rust+OpenSSL build that
fails here).

## Launch (background)

```bash
cd "/Users/jared/Desktop/Weather Model"
FORECAST_LOG_GH_REPO=jaredmcelreath-a11y/Weather-Model \
  /Users/jared/Library/Python/3.9/bin/streamlit run app.py \
  --server.headless true --server.port 8599 --browser.gatherUsageStats false
```

`FORECAST_LOG_GH_REPO` makes settlements/forecast_log/betting_log read the
public data branch (what the cloud's `[github]` secret does) — real data, no
token needed. Without it the app runs on stale local jsonl files. Wait for
`curl -s http://localhost:8599/` to return 200 (~5s).

## Screenshot a page

Plain `chrome --headless --screenshot` captures only the pre-websocket
skeleton — useless for Streamlit. Use the CDP driver in this directory, which
waits for real rendered text:

```bash
python3 cdp_shot.py "http://localhost:8599/lab_page" out.png "Shadow Consensus" 8
```

Args: url, out.png, wait-for-text (a string unique to the rendered page),
extra seconds after match (charts need ~6-8). It also prints the page's
innerText — often enough to verify without opening the image.

Page paths come from the `st.Page` function names in app.py:
`/` (Forecast) · `/hourly_page` · `/accuracy_page` · `/edge_page` ·
`/render` (History) · `/lab_page` · `/journal_page` · `/status_page`.

## Gotchas

- Kill the server when done: `pkill -f "streamlit run app.py"`.
- History page needs Kalshi creds (not in env locally) — it renders but its
  portfolio section degrades; don't judge that section locally.
- The 60s autorefresh makes long-lived CDP sessions rerun the page; screenshot
  within the first minute or expect a mid-capture rerun.
