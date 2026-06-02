---
name: playwright-mcp
description: How to preview the built site, capture responsive screenshots (mobile 375 / tablet 768 / desktop 1280), and inspect the accessibility tree via Playwright MCP. Use when the Designer wants to self-verify a render, and as the QA's primary inspection tool. The MCP is text-based — it returns structured ARIA snapshots, not pixels, which is cheaper and more reliable than image-OCR.
---

# Playwright MCP — usage guide

## Setup (one-time, by the user — see SETUP.md)

```bash
claude mcp add playwright npx @playwright/mcp@latest
```

No API key required. First call will trigger Playwright to download Chromium (~150 MB).

## Local server

Designer must start a local server first so Playwright can hit a URL:

```bash
cd ./agent_workspace
python3 -m http.server 8000
# Site is now at http://localhost:8000
```

## Key tools the MCP exposes

| Tool | Purpose | Typical args |
|---|---|---|
| `browser_navigate` | Open a URL | `{ "url": "http://localhost:8000" }` |
| `browser_snapshot` | Get accessibility-tree of current page (text, not pixels) | (no args) |
| `browser_take_screenshot` | Save a PNG at the current viewport | `{ "path": "./qa/mobile.png" }` |
| `browser_resize` | Change viewport | `{ "width": 375, "height": 812 }` |
| `browser_click` | Click an element by accessible name/role | `{ "selector": "..." }` |
| `browser_press` | Keyboard input (e.g., Tab for focus check) | `{ "key": "Tab" }` |
| `browser_list_tabs` / `browser_close` | Tab management | |

(Exact tool names vary slightly by Playwright MCP version — list available tools first if unsure.)

## Standard QA breakpoint sweep

```
1. browser_navigate to http://localhost:8000
2. browser_resize 375 x 812    → browser_take_screenshot ./qa/mobile.png
3. browser_resize 768 x 1024   → browser_take_screenshot ./qa/tablet.png
4. browser_resize 1280 x 800   → browser_take_screenshot ./qa/desktop.png
5. browser_snapshot            → inspect heading order + alt-text presence
6. browser_press "Tab" (repeatedly) → verify focus ring is visible
```

## Why accessibility-tree > screenshot

The MCP returns structured data like:

```
- heading "the project" [level=1]
  - paragraph "<dates from project_brief.md> ..."
- button "Plan your visit"
- image alt="A child looks through a microscope at a Science Museum workshop"
```

You can read this directly to check:
- Heading hierarchy (one h1? skips levels?)
- Every image has alt text
- Buttons have descriptive names
- Link text isn't "click here"

This is faster and more reliable than asking the LLM to read pixels.

## When to take an actual screenshot

- 3 breakpoint PNGs for the QA report (mobile / tablet / desktop)
- A "before/after" pair if the Designer is iterating on a specific component
- Don't screenshot every state — they cost tokens to interpret

## Output convention

Save QA evidence under `./agent_workspace/qa/`:
- `mobile.png`, `tablet.png`, `desktop.png`
- `report.md` — the prioritized fix list
