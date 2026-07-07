# Mobile High/Low Floating Toggle

**Date:** 2026-07-06
**Status:** Approved, ready for implementation plan

## Problem

On phones, the two forecast sections (High and Low) stack vertically, so seeing
the Low for the selected day means scrolling past the entire High section (chart,
distribution, market table, Top-3, Safest-hold) and back. The user wants to flip
between the High and Low for today/tomorrow with a single tap, without scrolling.

## Goal

A mobile-only floating bar pinned to the bottom of the screen that switches the
visible section between High and Low. Desktop is unchanged (both sections stay
side by side). Everything outside the two per-variable sections is unchanged.

## Scope

**In scope** — the two `render_variable` sections drawn by `render_page`
(`market_view.py`), which currently render into `cols[0]` (High) and `cols[1]`
(Low).

**Explicitly untouched:**
- Top metric row (Current Temp, Updated, Kalshi High/Low, Calib Bias, Day-Ahead σ)
  and the Kalshi "market as of" caption.
- Per-Source Breakdown expander.
- Model Accuracy expander.
- The entire desktop layout (viewport > 640px).

## Decisions (from brainstorming)

- **Toggle content:** each button shows the section label **plus the live
  consensus value** for the selected day — e.g. `HIGH 98°F` / `Low 78°F` — so both
  numbers are visible on the bar without tapping.
- **Default section (no explicit choice):** follow the existing "Featured" logic —
  **High on Today, Low on Tomorrow.**
- **Mechanism:** client-side (CSS + a tiny JS bridge). Toggling is instant, causes
  no Streamlit rerun, no re-fetch, and no scroll jump.

## Design

### 1. Section wrappers (identifiable in CSS)

In `render_page`, wrap each variable's render target in a keyed container so CSS
can target it:

- High → `st.container(key="wx_sec_high")`
- Low  → `st.container(key="wx_sec_low")`

Streamlit emits these as `.st-key-wx_sec_high` / `.st-key-wx_sec_low`. The
containers live *inside* the existing `st.columns(2)` columns, so the whole
column is targeted via `[data-testid="stColumn"]:has(.st-key-wx_sec_low)` — hiding
the column (not just its content) avoids a leftover empty-column gap when stacked.

### 2. Mobile visibility CSS (in `_inject_theme`, inside the existing `@media (max-width:640px)` block)

```
/* pre-JS default: show High until the bridge sets an explicit body class */
body:not(.wx-show-high):not(.wx-show-low)
  [data-testid="stColumn"]:has(.st-key-wx_sec_low){display:none!important;}

/* explicit selection (set by the JS bridge from hash or the featured default) */
body.wx-show-high [data-testid="stColumn"]:has(.st-key-wx_sec_low){display:none!important;}
body.wx-show-low  [data-testid="stColumn"]:has(.st-key-wx_sec_high){display:none!important;}

/* the floating bar itself (hidden on desktop; shown only here) */
.wx-toggle-bar{display:flex;}

/* keep the last content clear of the fixed bar */
[data-testid="stMainBlockContainer"]{padding-bottom:5.5rem!important;}
```

Desktop base rule: `.wx-toggle-bar{display:none;}` (outside the media query) so
the bar never appears above 640px. `:has()` is universally supported in current
(2026) mobile browsers.

### 3. Floating bar HTML (via `st.markdown`, rendered in `render_page` after the two sections)

A `position:fixed; bottom:0` bar with two buttons carrying a `data-wx-sel`
attribute and the live values:

```html
<div class="wx-toggle-bar">
  <div class="wx-toggle-btn" data-wx-sel="high">HIGH <b>98°F</b></div>
  <div class="wx-toggle-btn" data-wx-sel="low">Low <b>78°F</b></div>
</div>
```

- Values come from `pred["high"]["consensus"]` / `pred["low"]["consensus"]` for the
  currently selected day (`—` when that variable's block is `None`).
- Uses `<div role="button">`-style elements (not `<button>`/`onclick`) so the
  markdown sanitizer preserves them; click behavior is attached by the JS bridge.
- Styled with the active theme's CSS vars (`--surface`, `--ink`, `--accent`, etc.).
  The active button gets a `.wx-active` class (accent background).

### 4. JS bridge (via `streamlit.components.v1.html`, height 0)

A script that runs inside the component iframe and reaches the parent document
(`window.parent.document`, same-origin — the standard Streamlit pattern):

- Receives the server-computed default (`"high"`/`"low"`) as an injected constant.
- `current()` = URL hash (`#wxhigh` → `high`, `#wxlow` → `low`) if present, else the
  server default.
- `apply(sel)`: sets `wx-show-high`/`wx-show-low` on `<body>`, and toggles
  `.wx-active` on the matching button.
- Wires each `.wx-toggle-btn` click to: `history.replaceState(..., '#wxhigh'|'#wxlow')`
  (hash, not query param, so Streamlit ignores it; `replaceState` avoids a history
  entry and anchor-scroll) then `apply(sel)`.
- Polls briefly (setInterval, ~50ms, capped) until the bar exists in the parent DOM,
  since the component may execute before Streamlit paints the markdown.

**Persistence:** the hash survives the 60s `st_autorefresh` and Today/Tomorrow
switches, and `<body>` is not recreated on rerun — so the user's choice sticks.
With no hash, the featured default is applied fresh each render, so switching
Today↔Tomorrow follows the featured section until the user explicitly taps.

## Data flow

`render_page` already has `pred = snap[key]`, `feature_low = (key == "tomorrow")`.
Derive `sec_default = "low" if feature_low else "high"` and the two consensus
values, pass them into the bar HTML and the JS constant. No model/snapshot changes.

## Edge cases

- **`pred["high"]` or `pred["low"]` is `None`** ("No data"): the bar shows `—` for
  that value; toggle still switches (to an empty/warning section, as today).
- **First paint before JS runs:** the pre-JS CSS default shows High; on Tomorrow the
  bridge corrects to Low within ~50ms (brief, acceptable flash).
- **Component iframe:** rendered at height 0; if it leaves a visible sliver, collapse
  its block with CSS. It still executes JS while hidden.

## Testing

- Manual: load on a ≤640px viewport (or devtools device mode) — bar appears, tap
  swaps sections, values match the sections' Consensus metric, choice survives a
  60s refresh and a Today/Tomorrow switch. Load > 640px — no bar, both sections
  side by side, no layout change.
- Regression: existing `tests/` still pass (pure-Python; this change is
  presentation-only, so no unit-test surface — verification is visual).

## Non-goals

- No change to which sections exist, their content, or desktop layout.
- No toggle for the top metrics or the two lower expanders.
- No server-side viewport detection (all viewport logic is CSS media queries).
