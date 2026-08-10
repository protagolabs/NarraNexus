---
code_file: frontend/src/components/teams/ClearTeamDataDialog.tsx
last_verified: 2026-08-10
stub: false
---

# ClearTeamDataDialog.tsx — clear a team's chat / shared files

## Why it exists

Team counterpart to `ClearAgentDataDialog`. A team is a collaboration surface (group-chat
history + `_shared/teams/{id}` files); the owner needs a way to wipe that without deleting
the team. Two checkboxes — chat / files (chat defaults on, files opt-in) — map to
`api.clearTeamData(teamId, {chat, files})` → `DELETE /api/teams/{id}/data`. Danger-styled
confirm, disabled until a scope is picked.

## Upstream / downstream

- Rendered by [[AgentList]] (opened from the team row's [[TeamRowMenu]] ⋮ → "Clear data",
  mirroring how the agent clear-data dialog is opened from the agent row menu). AgentList
  owns the open/busy state (`clearTeamTarget` / `clearTeamBusy`).
- Backend: [[teams]] `_wipe_team_data` — keeps team, members, bus channel; deletes the
  room's `bus_messages` and/or the shared-files dir.

## 2026-08-10 — the files checkbox also takes the team's artifacts

Team artifacts are REQUIRED to live in `_shared/teams/{id}`, so deleting that
folder destroys their content and the backend cascades to their rows. The two
checkboxes are unchanged; what changed is what the second one MEANS.

This dialog is the only place a user is told, before pressing an irreversible
button, what it will do — so its copy (and the matching `optFilesDesc` strings)
is part of the behavior, not decoration around it. It had drifted: the cascade
was removed once and the copy updated to match, then re-introduced without the
copy being restored, leaving the dialog promising artifacts would be kept while
the server deleted them.
- i18n under `teams.clearData.*` (en+zh).
