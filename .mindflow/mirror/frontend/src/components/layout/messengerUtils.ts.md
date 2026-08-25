---
code_file: frontend/src/components/layout/messengerUtils.ts
last_verified: 2026-08-25
stub: false
---

# layout/messengerUtils.ts — Pure helpers for the sidebar Messenger row

## Why it exists

[[MessengerSection.tsx]] needs two pieces of derived state per agent: "what
should the second line preview" and "what order should rows appear in".
Both are pure functions of data already in the stores, so they're extracted
here to be unit-tested without mounting the component or touching Zustand —
see `__tests__/messengerUtils.test.ts`.

## How it works / design

- **`computeRowMeta`** mirrors the preview-derivation logic that used to
  live in the deleted `AgentList.tsx`'s `getRowMeta` (git history, removed
  2026-08-24 doc entry in [[Sidebar.tsx]]): prefer the freshest LOCAL session
  message over the server's `last_assistant_preview`/`last_assistant_at`, so
  a row updates the instant you talk to that agent, before the next
  `/api/auth/agents` refresh. Deliberately does **not** compute an unread
  count — Messenger is a read-only quick-switcher, not the old roster, and
  unread bookkeeping stays scoped to the Agents dashboard table.
- **`sortAgentsByActivity`** is the same "most-recently-active floats to the
  top" rule as the deleted `agentGroupUtils.ts`, reimplemented locally
  instead of reviving that file — it's being removed elsewhere on this
  branch in favor of the dashboard tables, and Messenger's sort has no
  grouping/collapse-state concerns to share with it. Score = max(server
  `last_assistant_at`, caller-supplied local activity ms, `created_at`
  floor); ties break by `agent_id` so equal timestamps don't reorder rows
  between renders.
- Both functions take plain data (`MessengerAgent`/`MessengerMessage`
  shapes), not `AgentInfo` directly, so they don't couple to the wider
  types module.

## Gotchas

- `computeRowMeta` returns `timeMs` (epoch-ms), not a formatted string —
  the caller formats with `formatChatTimestamp` so this module stays free
  of locale/timezone concerns and is trivial to test with fixed epoch
  values.
