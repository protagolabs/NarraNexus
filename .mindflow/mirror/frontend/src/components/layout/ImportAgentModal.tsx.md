---
code_file: frontend/src/components/layout/ImportAgentModal.tsx
last_verified: 2026-07-21
stub: false
---

# layout/ImportAgentModal.tsx — Agent Migration UI (import from other frameworks)

## Why it exists

The frontend of Agent Migration: turn an other-framework agent (Claude Code /
Codex / OpenClaw / Hermes) found on the local machine into a NarraNexus agent.
Launched from [[CreateMenu]]'s "Create Agent (from other source)" item.

Three linear stages, each one API call:
- **detect** → `api.migrateDetect()` (`GET /api/migrate/detect`) — list
  frameworks found in standard home locations; the user picks one or types a
  folder path.
- **preview** → `api.migrateScan()` (`POST /api/migrate/scan`) — extract that
  source into the standardized JSON and show a read-only summary (skills /
  memory / MCP counts, per-skill copy-vs-marketplace, plaintext-credential
  warning, narrative note).
- **done** → `api.migrateApply()` (`POST /api/migrate/apply`) — create +
  populate the agent, then render per-dimension result counts.

## Design decisions

- **Local only.** detect/scan read the user's filesystem and 503 on cloud, so
  [[AgentList]] only mounts this modal (and passes `onImportAgent`) when
  `useRuntimeStore.mode === 'local'`.
- **Preview before write.** Nothing is written until the user confirms on the
  preview stage — the scan is a pure read, so "Back" simply discards it.
- **Plaintext-credential warning** is surfaced when any MCP server has
  `secret_fields` (Owner decision: carry MCP creds, show them, warn).
- **Narrative is not imported** — the preview only *notes* that the agent will
  self-author a Narrative from `session_summary_seed` on first run.
- On success, delegates store wiring to the parent via `onApplied(result)`
  (AgentList refreshes the agent list + selects the new agent) — same pattern
  as `useCreateAgent`, so the two create paths don't drift.

## Gotchas

- Types mirror the Python schema ([[migration]]); the `/scan` output is POSTed
  back to `/apply` verbatim, so the shapes must stay in lock-step.
