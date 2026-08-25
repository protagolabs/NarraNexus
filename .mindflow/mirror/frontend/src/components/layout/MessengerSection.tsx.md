---
code_file: frontend/src/components/layout/MessengerSection.tsx
last_verified: 2026-08-25
stub: false
---

# layout/MessengerSection.tsx — Sidebar "Messenger" quick-switch row

## Why it exists

2026-08-24 retired the sidebar's chat roster (`AgentList` and friends) in
favor of the Agents/Squads dashboard tables — see [[Sidebar.tsx]]'s
changelog. That made "jump back into a specific recent conversation" a
three-hop path (nav → dashboard table → row's chat icon). The Owner asked
for a fast path back: a single collapsible nav row, styled identically to
Settings/System, that expands in place into a most-recent-first list of
every agent's last message. It is explicitly **not** a revival of
`AgentList` — no rename/clear/delete/public-toggle actions live here; those
stay on the dashboard table rows. This is read-only quick access only.

2026-08-25 (2): the first cut only listed agents, dropping the team rows
`AgentList` used to interleave at the top. The Owner asked for team rooms
back in the same quick-switcher — not a second list, one merged
most-recent-first feed spanning both, so a team room that just got a reply
outranks a quiet agent chat exactly the way it would have in `AgentList`.

## How it works / design

- Rendered by [[Sidebar.tsx]] as Zone 2b — the slot right after the
  Configure `<div>` (System is the last row in it) that used to be a bare
  `<div className="flex-1" />` spacer pushing the footer down. This
  component now **is** that spacer: its root is `flex-1 min-h-0` so it
  owns 100% of the space between Configure and the footer, not just the
  row itself. First attempt nested the row inside the Configure `<div>`
  with a `max-h-[280px]` list, which left a dead gap between the capped
  list and the footer — Owner flagged it 2026-08-25 (2); moving the whole
  component into Zone 2b and making the expanded list itself
  `flex-1 min-h-0 overflow-y-auto` fixed both the placement and the "rows
  are too small" feedback in the same pass (avatar `xs`→`sm`, more padding).
- The row itself carries no "+" new-chat affordance (Owner ruling,
  2026-08-25) — its only behavior is expand/collapse. Persisted open/closed
  via `localStorage['sidebar_messenger_open_v1']`, default closed.
- Expanded rows come in two kinds, both read-only:
  - Agent row (`MessengerRow`): `AvatarWithStatus` + `RingAvatar` (species
    `silicon`) → agent name as the row **title** (not a synthetic "chat
    title" — Owner ruling) → mono timestamp, then a second line with the
    last-message preview. Status dot is `warning` while the agent is
    streaming (`isAgentStreaming` or `active_run` present), `success`
    otherwise.
  - Team row (`TeamMessengerRow`): `AvatarWithStatus` + `GroupAvatar`
    (carbon·silicon split, same visual as the deleted `TeamChatRow`) →
    team name → mono timestamp → last-message preview, sourced from
    `TeamWithMembers.last_message_at`/`last_message_preview` (server-only —
    the sidebar never loads a team transcript, so there's no local-session
    fallback the way an agent row has). Status dot uses `teamHasUnread`
    (`info` = unread, `neutral` = read) instead of a streaming state — a
    team room has no single "is it running" signal cheap enough to compute
    here (`TeamMemberActivity` is per-member and not loaded by this
    component).
  - Exactly one row reads as "current" at a time: the agent highlight is
    suppressed (`!activeTeamId && ...`) while a team room's route
    (`/app/teams/:id/chat`) is open, mirroring `AgentList`'s
    `activeTeamChatId ? null : agentId` rule.
- Sort + preview derivation are pure functions imported from
  [[messengerUtils.ts]] so they're unit-tested independently of this
  component: `sortMessengerItems` interleaves agents and teams on one
  activity clock (`agentActivityScore` / `teamActivityScore`, both newest-of
  last-message-time/created_at) and returns `{kind, id}` rows that this
  component looks back up in `agentById`/`teamById` maps. The
  `activitySignature` / `teamsActivitySignature` memo-key tricks (id +
  message count/last-message-at, joined to a string) keep the resort off
  the per-token streaming hot path — same rationale as the deleted
  `AgentList.tsx`'s identical pattern (iron rules #14/#16: long-running
  agent streams must not make the sidebar itself become the bottleneck).
- Clicking an agent row: `setAgentId` + `setActiveAgent` (only if it's not
  already the active agent) then `navigate('/app/chat')` unless already
  there — same selection logic `AgentList.handleSelectAgent` used. Clicking
  a team row navigates to `/app/teams/:id/chat`; `TeamChatPanel` owns
  marking that room's read watermark on open (`markTeamRead`), so this
  component doesn't duplicate that bookkeeping.
- Agents are **not fetched here** — `useAutoRefresh` (mounted in
  `ChatView`) already keeps `configStore.agents` warm. Teams ARE primed
  here (`if (!teamsLoaded) teamsRefresh()`), same guard `AgentList` used,
  because nothing else on the chat surface guarantees `teamsStore` is
  loaded before this row's first expand.

## Upstream / downstream

- Depends on `useConfigStore` (`agentId`, `agents`, `setAgentId`),
  `useChatStore` (`agentSessions`, `setActiveAgent`, `isAgentStreaming`),
  `useTeamsStore` (`teams`, `loaded`, `refresh`), `@/lib/unread`'s
  `latestMessageMs`/`teamHasUnread`, `@/lib/utils`'s `formatChatTimestamp`,
  `@/components/nm`'s `GroupAvatar`, and [[messengerUtils.ts]].
- Rendered only by [[Sidebar.tsx]].

## Gotchas

- The empty state (`sidebar.messengerEmpty`) renders when the merged
  `items` list is empty, not when either store is loading — there's no
  owned loading state here since fetching isn't this component's job.
- The root needs `flex-1 min-h-0` and the expanded list needs its own
  `flex-1 min-h-0 overflow-y-auto` — drop either one and either the
  component stops filling Zone 2b again, or a large agent+team count pushes
  the sidebar's footer (Zone 3) off-screen instead of scrolling internally.
- `computeTeamRowMeta` returns an empty preview whenever
  `last_message_preview` is falsy, even if `last_message_at` is somehow
  set — per [[teams.ts]]'s `TeamWithMembers` doc, the three last-message
  fields are only ever null together, so this asymmetric check is a
  defensive default, not an observed real case.
