---
code_file: frontend/src/components/layout/TeamChatRow.tsx
last_verified: 2026-06-23
stub: false
---

# layout/TeamChatRow.tsx — One team's group-chat entry in the sidebar

## Why it exists

The sidebar groups chats into a **TEAMS** section (group chats) over an
**AGENTS** section (every agent once); see [[AgentList]]. This is one row in the
TEAMS section — extracted out of [[AgentGroupSection]] so teams live in their own
top section instead of being interleaved with their member agents (which also
made an agent in two teams appear twice).

## How it works / design

- Row sized like an [[AgentRowMenu]]-bearing agent row: a carbon·silicon split
  `GroupAvatar` (the team is human+AI), the team name, and `Group chat · N
  agents`.
- Owns its OWN inline-rename + `menuOpen` state. The ⋮ menu ([[TeamRowMenu]])
  sits right next to the name (Rename / Delete). `onRename`/`onDelete`/`onOpen`
  are thunks up to [[AgentList]] (teamsStore.updateTeam / deleteTeam / navigate
  to `/app/teams/:id/chat`).
- `active` (the team whose group chat route is open) paints `--nm-row-active`.
- Gotcha: `onOpen` is suppressed while renaming so clicking the input doesn't
  navigate; `menuOpen` lifts the row's z-index so the ⋮ panel paints above
  sibling rows (each row is its own stacking context).
