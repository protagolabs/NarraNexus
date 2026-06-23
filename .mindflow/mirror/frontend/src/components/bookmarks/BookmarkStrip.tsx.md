---
code_file: frontend/src/components/bookmarks/BookmarkStrip.tsx
last_verified: 2026-06-20
stub: false
---

## 2026-06-20 — cleaner strip: no text headers, carbon highlight, centered

Owner-driven polish:
- **Category text headers removed** (CONFIG / ACTIVITY / …). Groups are now
  divided by a hairline only (`border-t` between categories) — the strip reads
  as icon-only. `category.label` survives solely as the React key.
- **Carbon (orange) highlight**: hover and active now tint the tab — bg
  `--color-carbon-soft`, icon + caption `--color-carbon`, and the active
  "bookmark tongue" edge rule is carbon (was ink/`--text-primary`). Resting
  icons stay `--text-tertiary` so the species color reads as accent, not noise
  (Axiom #1). Implemented with a `group` + `group-hover:` on the button.
- **Centered**: caption gained `text-center`; icon/caption already centered.
- MCP caption now "MCP" (see [[tabs]] `stripLabel`).

# BookmarkStrip.tsx — Right-edge strip, atomic-tab IA

## 2026-06-11 (PM) — 64px strip: icon + caption, horizontal category headers

Owner: icons alone were not understandable. Strip widened 36→64px;
each atomic tab is icon + 8px mono caption (stripLabel ?? label);
category headers are horizontal micro-labels instead of rotated text.

## 为什么存在

~36px of right edge is the ONLY persistent footprint; chat + artifacts
get the rest. 2026-06-11 Owner revision: the first iteration (2 big
bookmarks opening multi-section panels) was rejected — "每一个小标签页
里面就一个内容". The smallest unit is now an atomic tab: ONE tab opens
exactly ONE panel; categories ([[tabs]] STRIP_CATEGORIES) only group
visually.

## 设计决策

- All structure lives in the [[tabs]] registry — strip just renders it.
- Live signals overlay the owning tab (derived per render via
  deriveTabStatus): failed jobs → carbon pulse + count badge; running
  job → corner spinner; inbox unread → pulse + count; awareness
  external update → yellow info dot.
- Active tab gets a 2px inset ink rule on the outer edge — the visible
  tongue of an inserted bookmark.
- Category labels are rotated 8px mono dividers, aria-hidden (tabs
  carry their own aria-labels).
- Strip scrolls vertically (scrollbar hidden) on short windows.

## 新人易踩的坑

data-help-id anchors (`bookmarks.<tabId>`, `bookmarks.strip`) are help
overlay contract — keep in sync with [[helpContent]].
