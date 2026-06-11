---
code_file: frontend/src/components/bookmarks/BookmarkStrip.tsx
last_verified: 2026-06-10
stub: false
---

# BookmarkStrip.tsx — Right-edge vertical bookmark strip

## 为什么存在

Workstream A (spec 2026-06-10) replaces the permanent 5-tab context
panel with paper-edge index bookmarks: ~36px of right-edge strip is the
ONLY persistent footprint; chat + artifacts get the rest. The metaphor
extends the NM "warm paper" motif — plastic index tabs on a book page.

## 上下游关系

- **被谁用**: MainLayout's ChatView (renders strip + [[BookmarkDrawer]]).
- **依赖谁**: [[bookmarkStore]] (highlights / subBookmarks / badges,
  `visibleSubBookmarks` derivation), Radix tooltip, lucide icons.

## 设计决策

- **Two fixed big bookmarks only** (ACTIVITY / AGENT) — the bookmark
  metaphor carries its own quantity discipline; system vocabulary
  (Runtime / Awareness / Narrative / MCP) never appears at this level.
- Sub-bookmark routing is by key prefix: `job:*` and `inbox` belong to
  activity; `profile:*` to agent. Keys are minted by bookmarkStore.
- Big-bookmark aggregate: any 'attention' under it → carbon pulse;
  else any 'info' → static yellow dot. Activity's numeric badge is
  `failedJobs + inboxUnread` (the persistent badge layer only — info
  highlights never produce numbers).
- 'running' renders a spinner and is deliberately NOT a highlight
  (spec §5.3): an in-flight job doesn't need the user.
- **Peek animation is a mount animation, not tracked state**: small
  bookmarks are keyed by bookmark key, so a newly appearing key mounts
  a fresh node and `animate-bookmark-peek` (index.css) plays exactly
  once. No seen-key bookkeeping — render-time ref access is forbidden
  by the React Compiler lint rules anyway.
- Labels live in `aria-label` + tooltip; the 36px strip has no room
  for horizontal text, and the rotated big-bookmark labels reuse the
  ctx-tabs mono-uppercase type language.

## 新人易踩的坑

`visibleSubBookmarks` is called per-section with a synthetic state
slice (only that section's subs) so the max=3 cap applies per big
bookmark, not globally.
