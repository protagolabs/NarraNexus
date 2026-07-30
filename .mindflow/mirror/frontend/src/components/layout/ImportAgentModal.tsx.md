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

Four linear stages:
- **framework** — `api.migrateDetect()` (`GET /api/migrate/detect`) runs once on
  open; detections are grouped by framework and the user picks a framework
  first (or types a folder path to skip straight to preview).
- **source** — the per-source list for the chosen framework. This exists
  because Claude Code returns one detection PER PROJECT (see [[detector.py]]):
  framework-first, then drill into the project list. Single-source frameworks
  still show a one-row list for a consistent two-step flow.
- **preview** — `api.migrateScan()` (`POST /api/migrate/scan`) — extract that
  source into the standardized JSON. The preview is **editable**: the agent name
  is an input (defaults to the scanned name), and the sessions render as a
  checkbox list — all checked = per-project import (every session → a Narrative),
  one checked = per-session. Also shows skills / memory / MCP counts, per-skill
  copy-vs-marketplace, and the plaintext-credential warning.
- **done** — `api.migrateApply()` (`POST /api/migrate/apply`) — create +
  populate the agent, then render per-dimension result counts (incl. Narratives
  created + memory turns retained).

On apply the modal sends a TRIMMED `import_data`: the renamed agent + only the
checked sessions (`sessions.filter(selected)`) — no backend change needed, the
applier just imports fewer sessions. Back navigation is stage-wise:
preview → source → framework.

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
- Claude Code detect returns **one row per project** (see [[detector.py]]), all
  labeled "Claude Code" — `detectionTitle` suffixes the project folder name and
  `detectionHint` shows the session count / `shared config` fallback marker so
  the repeated rows are distinguishable.
