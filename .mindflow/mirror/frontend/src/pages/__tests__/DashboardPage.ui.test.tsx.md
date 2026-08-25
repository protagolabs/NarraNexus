---
code_file: frontend/src/pages/__tests__/DashboardPage.ui.test.tsx
last_verified: 2026-08-25
stub: false
---

# DashboardPage.ui.test.tsx — Agents dashboard information-architecture guard

## Why it exists

The Agents landing page deliberately keeps creation and search prominent while
omitting the old KPI strip, team dropdown, Agents/Squads switch, bulk mutation
bar, checkboxes, and selection summary. It also locks the Framework and Model
columns to real per-agent values. This test protects that product-level
hierarchy so retired controls are not accidentally restored later.

Heavy dashboard detail children and stores are replaced with narrow test
doubles because this suite owns only the page shell; polling, expanded status
details, and row menus have their own component or store boundaries.

The Agent fixture includes two `bound_channels`; the suite requires the new
Channels header and two rendered channel-icon triggers. This prevents the
column from regressing to per-row fetch state or silently dropping bindings.

The row-navigation case asserts that clicking an Agent changes location to
`/app/agents/:agentId`. This is the guard against restoring the retired inline
expand interaction.

The Agent chat action must expose the accessible “Open chat” name and render
the visible localized “Chat” label. This prevents the action from regressing
to the retired icon-only affordance while preserving row/Profile navigation.
The `AgentRowMenu` double deliberately renders a marker, and the page must not
mount it; configuration now belongs to the Agent Profile Settings tab.

The status fixture also carries a fixed `last_activity_at`; the suite asserts
the Last active header and the raw ISO tooltip, proving the column is wired to
the existing dashboard-status payload rather than a new per-row request.

The typography assertions keep the table header, agent ID, and model value out
of the monospace font family. The directory deliberately uses one sans-serif
family throughout and creates hierarchy through weight, size, and color.

Framework and Model cells must each contain a rendered SVG or image brand
mark next to their text. This keeps the dashboard wired to the shared brand
icon system without asserting vendor-specific SVG path data.

The Agents directory wrapper intentionally has no outer `border` class, and
the shell contains no `border-b` or `border-t` table separators. The Team
profile itself may have a border because its portal is a floating surface,
not part of the table grid.

The Teams-cell fixture carries one team and verifies that the visible trigger
contains only its initials inside a `GroupAvatar`, that the cell uses the
overlap spacing contract, and that the avatar receives `carbon,silicon` in
the order required for the reference UI's blue-left/orange-right ring. The
associated profile content must carry the description and member count.
An isolated empty-membership case requires the Teams cell to render `—`
instead of the retired `untagged` label.
Tooltip primitives are narrowed to static test doubles here; Radix owns
pointer/focus activation, while this suite owns the dashboard's trigger and
profile payload.

## 2026-08-25 — Teams tab: member avatars, overflow, and the retired Manage button

A new `describe` block covers the Teams tab now that its Members column
renders real `TeamMemberAvatars` (see
[[../../components/agents/TeamMemberAvatars.tsx]]) instead of a plain count.
The `@/components/nm` double gained a `StatusDot` export — omitting it would
crash the tab the moment a member avatar tries to render its status dot,
silently passing every other test in this file since none of them render
`?view=teams`.

The five-member fixture (only three visible, `agent-4`/`agent-5` folded into
the overflow badge) asserts the `max={3}` cap end-to-end, that a member's
hover-card fields (name, description, formatted framework, model) actually
reach the DOM, and that clicking a member avatar — not the row — is what
navigates to `/app/agents/:id`. It also asserts no `/manage/i`-named button
survives, guarding against the deleted "Manage" button reappearing.

The Created-by assertion needed its own `data-testid="team-created-by"`
(added to the page) because the fixture's `displayName` ("Owner") also shows
up inside a visible member's hover-card Owner row once the fixture agents
are all owned by the same test user — `getByText('Owner')` alone is
ambiguous once member avatars carry owner metadata too.

## 2026-08-25 — Member cap tightened to 2; Team avatar and Creator species covered

The five-member fixture's cap assertion moved from 3 to 2 visible avatars
(now `agent-2`+ overflows starting at `agent-3`) to track
[[../../DashboardPage.tsx]]'s `max={2}`. The `RingAvatar` double now forwards
`species` as `data-species` — needed to assert the Created-by avatar renders
`carbon` (human) rather than `silicon` (agent), which the Leader column still
uses. A new test asserts the Team-name cell's color dot became an avatar
(`team-avatar-team-1`) wrapping the same `GroupAvatar` used by
`MessengerSection.tsx`'s team row — asserted via the shared `[data-nm=
"group-avatar"]` marker, `"RT"` initials, and `data-species="carbon,silicon"`
— instead of the retired plain `<span>` color swatch or a custom-color ring
(an intermediate design this file's history covers and superseded).
