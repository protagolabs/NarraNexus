---
code_file: frontend/src/components/bookmarks/ActivityPanel.tsx
last_verified: 2026-06-10
stub: false
---

# ActivityPanel.tsx — "What is/was my agent doing" drawer panel

## 为什么存在

Spec §3: the old Runtime-Execution / Jobs / Inbox tabs all answered the
same user question. Execution moved inline into the chat (TurnTimeline);
Jobs + Inbox merge here as the drawer content behind the ACTIVITY big
bookmark.

## 上下游关系

- **被谁用**: BookmarkDrawer children in MainLayout's ChatView.
- **依赖谁**: JobsPanel / AgentInboxPanel in `embedded` mode (their
  content logic untouched), [[bookmarkStore]] (markOpened on deep-link,
  resolveJobAttention via JobsPanel's onJobResolved callback).

## 设计决策

- **Two stacked sections, NOT an interleaved feed** (spec §14.1):
  merging the timelines needs a unified timestamp semantic Jobs and
  Inbox don't share yet. Sections first; interleave is a possible
  later iteration.
- Deep-link granularity is the SECTION for v1 — row-level focus would
  need JobsPanel to accept an external expandedId. Recorded as known
  follow-up, not silently dropped.
- Failed-job resolution flows through JobsPanel's `onJobResolved`
  (fired after successful cancel/resume) so this panel never forks
  job-action logic.

## 新人易踩的坑

markOpened fires on mount per focusKey — it clears 'info' highlights
only; a failed job's 'attention' stays until resolveJobAttention.
