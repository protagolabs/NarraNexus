---
code_file: frontend/src/components/agents/TeamMemberAvatars.tsx
last_verified: 2026-08-25
stub: false
---

# TeamMemberAvatars.tsx — Agent-list-style member avatars for a Team row

Mirror image of [[AgentTeamAvatars.tsx]]: that component shows which Teams an
Agent belongs to (on an Agent row); this one shows which Agents belong to a
Team (on a Team row), one Tooltip-triggered avatar per member instead of a
plain member-count string. Both live side by side in `components/agents/`
because they're the same "avatar + hover Profile" idiom applied in opposite
directions — [[../../pages/DashboardPage.tsx]]'s Teams tab is the only current
caller.

## How it works

Renders `memberAgentIds` as overlapping `RingAvatar`s (`-space-x-2`), capped at
`max` (caller passes `3` from the Teams tab to fit the column) with a `+N`
overflow bubble past that; the overflow bubble is a dead-end Tooltip (member
count only), not a link — no roster-browsing surface exists yet to send it to.

Each visible avatar is a Tooltip trigger AND a navigate button in one: hover/
focus opens a mini profile card (name, Lock/Globe for `is_public`, a status
dot + label via a locally-computed status cell, description, then Runtime/
Model/Owner rows), and clicking calls
`navigate('/app/agents/:id', { state: { from: 'dashboard' } })` — the same
destination and breadcrumb-origin tag the Agents table's whole-row click
already uses, so the Profile page's back button behaves identically regardless
of which table sent the user there.

Member data comes from two maps the caller already had lying around
(`agentsById` from `rosterAgents`, `statusById` from the live status feed) —
no new requests. `agentsById` only contains agents the viewer owns/rosters, so
a team member owned by someone else (shared/public agent in the team) resolves
to `undefined`: the avatar still renders (id-derived initials, id shown as the
tooltip name), but Runtime/Model fall back to `—` and Owner shows the raw
`created_by` id. This is the same gap the Leader column has always had for
non-rostered leaders — not something this component introduces or attempts to
fix.

## 2026-08-25 — Overflow badge is plain text, not a `RingAvatar`

The `+N` overflow indicator past `max` was originally a `RingAvatar
species="neutral"` with `+N` as its label — Owner ruling: no ring here, it
should read as plain small text, not another avatar. It's now a bare
`+N` span (still the same Tooltip trigger, still shows the total member
count on hover); only the visible members get the ring-avatar treatment.

## Gotchas

- **`formatFramework` and the status-cell mapping are local copies**, not
  imports from [[../../pages/DashboardPage.tsx]] or
  [[../../pages/AgentProfilePage.tsx]] — those two files already duplicate
  `formatFramework` between themselves deliberately (Owner ruling: keep them
  independent rather than share, see the comment next to
  `agentChatButtonClass` in `AgentProfilePage.tsx`). This component follows
  that same precedent instead of introducing a shared util the Owner has
  already rejected once.
- **No hostname/runtime-host field exists anywhere in `AgentInfo`** — the
  Runtime row shows only the formatted framework label (e.g. "Claude Code"),
  never a machine name. Don't invent one without a backend field to back it.
- **Empty Model shows `—`**, matching the rest of the app — no bespoke
  "runtime default" string was introduced for the empty case.
- The wrapping `<span>` stops click propagation (same as `AgentTeamAvatars`)
  so a stray click inside the avatar cluster can't misfire a future row-level
  action; today the Teams tab row itself has no click handler, so this is
  purely defensive.
