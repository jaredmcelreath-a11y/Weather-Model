# Mobile High/Low Floating Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mobile-only floating bar (pinned to the bottom of the screen) that swaps the visible section between the day's High and Low, so a phone user never scrolls past a full section to reach the other.

**Architecture:** Both sections render as today (two `st.columns`). Two pure helper functions build (a) the bar's HTML and (b) a tiny JS bridge. On mobile only, CSS `:has()` hides the non-selected column; the JS bridge — running in a `components.html` iframe and reaching the same-origin parent document — toggles a `<body>` class on button tap and stores the choice in the URL hash so it survives the 60s auto-refresh. Desktop is entirely unchanged (all new behavior is gated behind `@media (max-width:640px)` and a `.wx-toggle-bar{display:none}` default).

**Tech Stack:** Streamlit (`st.markdown` raw HTML, `streamlit.components.v1.html`), CSS (`:has()`, media queries), a small vanilla-JS bridge. Tests: pytest, importing pure helpers from `market_view` (mirrors `tests/test_prob_chart.py`).

## Global Constraints

- Mobile breakpoint is **`max-width:640px`** — reuse the existing media-query block in `_inject_theme`; do not introduce a new breakpoint.
- **Desktop (>640px) rendering must not change at all** — both sections stay side by side; the bar is `display:none` by default and only shown inside the media query.
- **Do not touch** the top metric row, the Kalshi "market as of" caption, the Per-Source Breakdown expander, or the Model Accuracy expander.
- Style the bar with the active theme's CSS variables (`--surface`, `--surface2`, `--ink`, `--muted`, `--accent`, `--accent-strong`, `--border`, `--bg`) — never hardcoded colors.
- Persist the user's choice in the **URL hash** (`#wxhigh`/`#wxlow`) via `history.replaceState`, never a Streamlit query param (query params trigger reruns; the hash is ignored by Streamlit).
- Default section (no explicit choice) follows the **Featured** logic already in `render_page`: `feature_low = (key == "tomorrow")` → default `"low"` on Tomorrow, `"high"` on Today.

---

### Task 1: Pure helper functions for the bar HTML and JS bridge

Factor the two string-producing pieces into pure functions so they're unit-testable in isolation (the rendering wiring comes in Task 2). Both live in `market_view.py` alongside the other render helpers (e.g. near `cents`/`spread_c`, above `render_variable`).

**Files:**
- Modify: `market_view.py` (add two module-level functions)
- Test: `tests/test_mobile_toggle.py` (create)

**Interfaces:**
- Consumes: nothing (pure functions).
- Produces:
  - `mobile_toggle_bar_html(high_d, low_d) -> str` — `high_d`/`low_d` are the per-variable snapshot blocks (`pred["high"]` / `pred["low"]`), each a dict with a `"consensus"` key, or `None`. Returns the bar's HTML string.
  - `mobile_toggle_bridge_js(default: str) -> str` — `default` is `"high"` or `"low"`; returns a full `<script>…</script>` string. Raises `ValueError` on any other `default`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mobile_toggle.py`:

```python
"""Unit tests for the mobile High/Low floating toggle helpers in market_view.

Presentation-only helpers: they build the bar's HTML and the JS bridge string.
Pure functions (no Streamlit, no network), mirroring tests/test_prob_chart.py.
"""

import pytest

from market_view import mobile_toggle_bar_html, mobile_toggle_bridge_js


def test_bar_shows_both_consensus_values():
    html = mobile_toggle_bar_html({"consensus": 98}, {"consensus": 78})
    # both buttons present, tagged for the JS bridge to wire
    assert 'data-wx-sel="high"' in html
    assert 'data-wx-sel="low"' in html
    # live values rendered on the bar
    assert "98°F" in html
    assert "78°F" in html
    assert "wx-toggle-bar" in html


def test_bar_handles_missing_blocks():
    # None block, or a block with no consensus, renders an em dash — not a crash
    html = mobile_toggle_bar_html(None, {"consensus": None})
    assert html.count("—") == 2
    assert "°F" not in html


def test_bridge_embeds_default_and_reaches_parent():
    js = mobile_toggle_bridge_js("low")
    # default selection is embedded for the no-hash first paint
    assert '"low"' in js
    # both hash tokens the bar toggles between
    assert "wxhigh" in js and "wxlow" in js
    # reaches the same-origin parent document (the Streamlit component pattern)
    assert "window.parent" in js
    # it's a script block ready for components.html
    assert js.strip().startswith("<script>")
    assert js.strip().endswith("</script>")


def test_bridge_rejects_bad_default():
    with pytest.raises(ValueError):
        mobile_toggle_bridge_js("middle")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_mobile_toggle.py -v`
Expected: FAIL with `ImportError: cannot import name 'mobile_toggle_bar_html'`.

- [ ] **Step 3: Implement the two helpers**

In `market_view.py`, add these two module-level functions (place them just above `def render_variable(` near line 628):

```python
def mobile_toggle_bar_html(high_d, low_d):
    """HTML for the mobile-only floating High/Low switcher bar.

    `high_d`/`low_d` are the per-variable snapshot blocks (each a dict with a
    'consensus' key, or None). Each button carries a data-wx-sel attribute the
    JS bridge wires up, and shows the day's live consensus so both numbers are
    visible without tapping. Hidden on desktop via CSS (.wx-toggle-bar default
    display:none); shown only inside the ≤640px media query."""
    def _v(d):
        if d and d.get("consensus") is not None:
            return f"{d['consensus']}°F"
        return "—"
    return (
        '<div class="wx-toggle-bar">'
        f'<div class="wx-toggle-btn" data-wx-sel="high">HIGH <b>{_v(high_d)}</b></div>'
        f'<div class="wx-toggle-btn" data-wx-sel="low">Low <b>{_v(low_d)}</b></div>'
        '</div>'
    )


def mobile_toggle_bridge_js(default):
    """JS bridge (for components.html) that makes the floating bar work.

    Runs inside the component's sandboxed-but-same-origin iframe and reaches the
    parent document (window.parent) — the standard Streamlit client-side hack.
    On tap it stores the choice in the URL hash (#wxhigh/#wxlow, ignored by
    Streamlit) and toggles a wx-show-high/wx-show-low class on <body>, which the
    CSS uses to hide the other column on mobile. With no hash it applies
    `default` (the featured section), so the choice persists across the 60s
    auto-refresh and Today/Tomorrow switches. `default` must be 'high' or 'low'."""
    if default not in ("high", "low"):
        raise ValueError(f"default must be 'high' or 'low', got {default!r}")
    return (
        "<script>\n"
        "(function(){\n"
        f'  var DEFAULT = "{default}";\n'
        "  function apply(pdoc, sel){\n"
        '    pdoc.body.classList.remove("wx-show-high","wx-show-low");\n'
        '    pdoc.body.classList.add(sel === "low" ? "wx-show-low" : "wx-show-high");\n'
        '    var btns = pdoc.querySelectorAll(".wx-toggle-btn");\n'
        "    for (var i=0;i<btns.length;i++){\n"
        '      btns[i].classList.toggle("wx-active",'
        ' btns[i].getAttribute("data-wx-sel")===sel);\n'
        "    }\n"
        "  }\n"
        "  function wire(){\n"
        "    var pdoc, ploc, phist;\n"
        "    try { pdoc = window.parent.document; ploc = window.parent.location;"
        " phist = window.parent.history; }\n"
        "    catch(e){ return true; }\n"
        '    var btns = pdoc.querySelectorAll(".wx-toggle-btn");\n'
        "    if (!btns.length) return false;\n"
        "    for (var i=0;i<btns.length;i++){\n"
        "      (function(btn){\n"
        "        btn.onclick = function(){\n"
        '          var sel = btn.getAttribute("data-wx-sel");\n'
        '          try { phist.replaceState(null, "",'
        ' sel === "low" ? "#wxlow" : "#wxhigh"); } catch(e){}\n'
        "          apply(pdoc, sel);\n"
        "        };\n"
        "      })(btns[i]);\n"
        "    }\n"
        '    var h = (ploc.hash || "").replace("#","");\n'
        '    apply(pdoc, h === "wxlow" ? "low" : (h === "wxhigh" ? "high" : DEFAULT));\n'
        "    return true;\n"
        "  }\n"
        "  var n = 0, t = setInterval(function(){"
        " if (wire() || ++n > 40) clearInterval(t); }, 50);\n"
        "})();\n"
        "</script>"
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -m pytest tests/test_mobile_toggle.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd "/Users/jared/Desktop/Weather Model"
git add market_view.py tests/test_mobile_toggle.py
git commit -m "feat: pure helpers for the mobile High/Low toggle bar + bridge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire the toggle into the page (containers, CSS, bar, bridge)

Give the two sections CSS-targetable wrappers, add the mobile CSS (hide the non-selected column, style + show the bar, pad the page bottom), and render the bar + JS bridge from `render_page`. This is the task that makes the feature actually work; after it, mobile toggles and desktop is unchanged.

**Files:**
- Modify: `market_view.py`
  - imports (top of file) — add `streamlit.components.v1`
  - `_inject_theme` — add bar base CSS + mobile rules
  - `render_page` (~lines 1084–1090) — keyed wrappers + bar + bridge

**Interfaces:**
- Consumes (from Task 1): `mobile_toggle_bar_html(high_d, low_d)`, `mobile_toggle_bridge_js(default)`.
- Produces: no new callables; behavioral change only.

- [ ] **Step 1: Add the components import**

In `market_view.py`, near the top imports (after `import streamlit as st`, around line 17), add:

```python
import streamlit.components.v1 as components
```

- [ ] **Step 2: Add the bar's base CSS (desktop-hidden)**

In `_inject_theme`, add these rules to the `<style>` string. Put them next to the other component rules — e.g. immediately after the `.wmini .wval{…}` block (around line 177, before the closing `"</style>"`):

```python
        # mobile High/Low floating switcher — hidden on desktop; the ≤640px block
        # below flips it to flex. Styled from the palette vars so it follows the
        # active theme. Buttons carry data-wx-sel; the JS bridge wires the taps.
        ".wx-toggle-bar{display:none;position:fixed;left:0;right:0;bottom:0;z-index:1000;"
        "gap:0.5rem;padding:0.5rem 0.7rem calc(0.5rem + env(safe-area-inset-bottom));"
        "background:var(--surface);border-top:1px solid var(--border);"
        "box-shadow:0 -6px 18px rgba(0,0,0,0.35);}\n"
        ".wx-toggle-btn{flex:1 1 50%;text-align:center;cursor:pointer;user-select:none;"
        "font-family:'Bitter',serif;font-weight:700;font-size:0.9rem;color:var(--muted);"
        "background:var(--surface2);border:1px solid var(--border);border-radius:10px;"
        "padding:0.6rem 0.4rem;white-space:nowrap;}\n"
        ".wx-toggle-btn b{color:var(--ink);}\n"
        ".wx-toggle-btn.wx-active{color:var(--bg);background:var(--accent);"
        "border-color:var(--accent-strong);}\n"
        ".wx-toggle-btn.wx-active b{color:var(--bg);}\n"
        # keep the zero-height JS-bridge component from adding vertical space
        ".st-key-wx_bridge,.st-key-wx_bridge iframe{height:0!important;min-height:0!important;"
        "margin:0!important;border:0!important;}\n"
```

- [ ] **Step 3: Add the mobile rules inside the existing media query**

In `_inject_theme`, find the `@media (max-width:640px){` block (starts ~line 97) and add these rules *before* its closing `"}\n"` (the one right after the `.stApp h1{font-size:1.7rem!important;}` line, ~line 114):

```python
        # show the floating switcher and keep the last content clear of it
        ".wx-toggle-bar{display:flex!important;}"
        "[data-testid=\"stMainBlockContainer\"]{padding-bottom:5.5rem!important;}"
        # pre-JS default: show High until the bridge sets an explicit body class
        "body:not(.wx-show-high):not(.wx-show-low) "
        "[data-testid=\"stColumn\"]:has(.st-key-wx_sec_low){display:none!important;}"
        # explicit selection from the JS bridge (hash or featured default)
        "body.wx-show-high [data-testid=\"stColumn\"]:has(.st-key-wx_sec_low)"
        "{display:none!important;}"
        "body.wx-show-low [data-testid=\"stColumn\"]:has(.st-key-wx_sec_high)"
        "{display:none!important;}"
```

- [ ] **Step 4: Wrap the sections and render the bar + bridge**

In `render_page`, replace the current block (lines 1084–1090):

```python
    feature_low = (key == "tomorrow")
    cols = st.columns(2)
    today_iso = snap["today"]["day"]
    render_variable(cols[0], "High", pred["high"], "high", pred["day"], adapter,
                    featured=not feature_low, safe_min=safe_min, today_iso=today_iso)
    render_variable(cols[1], "Low", pred["low"], "low", pred["day"], adapter,
                    featured=feature_low, safe_min=safe_min, today_iso=today_iso)
```

with:

```python
    feature_low = (key == "tomorrow")
    cols = st.columns(2)
    today_iso = snap["today"]["day"]
    # Keyed wrappers so the mobile CSS can hide the non-selected column via :has().
    with cols[0]:
        high_box = st.container(key="wx_sec_high")
    with cols[1]:
        low_box = st.container(key="wx_sec_low")
    render_variable(high_box, "High", pred["high"], "high", pred["day"], adapter,
                    featured=not feature_low, safe_min=safe_min, today_iso=today_iso)
    render_variable(low_box, "Low", pred["low"], "low", pred["day"], adapter,
                    featured=feature_low, safe_min=safe_min, today_iso=today_iso)

    # Mobile-only floating High/Low switcher (desktop keeps both columns). The bar
    # is plain HTML; the JS bridge (in a zero-height component) wires the taps and
    # persists the choice in the URL hash across the 60s refresh. Default follows
    # the featured section for the selected day.
    st.markdown(mobile_toggle_bar_html(pred["high"], pred["low"]),
                unsafe_allow_html=True)
    with st.container(key="wx_bridge"):
        components.html(mobile_toggle_bridge_js("low" if feature_low else "high"),
                        height=0)
```

- [ ] **Step 5: Smoke-test the import and full test suite**

Run: `cd "/Users/jared/Desktop/Weather Model" && python -c "import market_view" && python -m pytest tests/ -q`
Expected: import succeeds (no error) and the suite passes (including `tests/test_mobile_toggle.py`).

- [ ] **Step 6: Manual visual verification**

Run: `cd "/Users/jared/Desktop/Weather Model" && streamlit run app.py`
Then in the browser:
- **Desktop (window > 640px):** both High and Low sections are side by side; **no** bottom bar; layout identical to before.
- **Mobile (devtools device mode, or narrow the window ≤640px):**
  - A bottom bar shows `HIGH <val>` and `Low <val>`; the values match each section's "Consensus" metric for the selected day.
  - Only one section (the featured one — High on Today, Low on Tomorrow) is visible; the active button is highlighted.
  - Tapping the other button swaps the visible section instantly (no page reload/scroll jump) and moves the highlight.
  - Wait ~60s for the auto-refresh (or toggle the Day radio Today↔Tomorrow): the chosen section stays selected.
  - The last content (Model Accuracy expander) is not hidden behind the bar.

- [ ] **Step 7: Commit**

```bash
cd "/Users/jared/Desktop/Weather Model"
git add market_view.py
git commit -m "feat: mobile-only floating High/Low section toggle

Bottom bar swaps the visible section on phones (client-side, no rerun);
desktop shows both columns unchanged. Choice persists via URL hash.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Why `:has()` on `stColumn`:** hiding the inner `.st-key-wx_sec_*` container would leave the (now empty) column taking layout space when stacked. Targeting the whole column via `[data-testid="stColumn"]:has(.st-key-wx_sec_low)` removes it cleanly. `:has()` is supported in all current (2026) mobile browsers.
- **Why a JS bridge / `components.html`:** `st.markdown` strips `<script>` and inline event handlers, so the bar's HTML can't carry its own click logic. `components.html` runs JS in a sandboxed **same-origin** iframe, from which `window.parent.document` is reachable — the standard Streamlit client-side pattern. The bridge polls (`setInterval`, capped at 40×50ms) because the component can execute before Streamlit paints the bar.
- **Why the hash, not a query param:** a query param would trigger a Streamlit rerun on every tap (the thing we're avoiding). The hash is invisible to Streamlit, and `<body>` isn't recreated on rerun, so the class + hash both survive the 60s refresh.
- **Presentation-only:** the only unit-test surface is the two pure helpers (Task 1). The wiring, CSS, and cross-document behavior are verified by the import smoke test + the manual checklist (Task 2, Step 6), consistent with the spec's testing section.
