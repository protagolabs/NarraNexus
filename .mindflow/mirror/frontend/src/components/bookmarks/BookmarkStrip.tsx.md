---
code_file: frontend/src/components/bookmarks/BookmarkStrip.tsx
last_verified: 2026-06-11
stub: false
---

# BookmarkStrip.tsx — Right-edge strip, atomic-tab IA

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
