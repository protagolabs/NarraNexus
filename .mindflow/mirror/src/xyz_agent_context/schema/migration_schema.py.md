---
code_file: src/xyz_agent_context/schema/migration_schema.py
stub: false
last_verified: 2026-07-21
---

## Why it exists

The framework-agnostic contract between the migration Scanner (produces it) and
the consumers — Migration Skill / Import Button (map+write from it). One shape
covers Claude Code / Hermes / OpenClaw / Codex / Custom.

## Design decisions

- `MigrationMcpServer.transport` discriminates `stdio` (command/args/env) vs
  `url` (url/headers). Both are captured so no source info is lost; only `url`
  writes in v1.0 (NarraNexus MCP data model is URL/headers only).
- **Credential policy**: MCP `env`/`headers` VALUES are carried (Owner decision
  2026-07-21 — MCP is useless without its auth; UI shows them plaintext + warns).
  Non-MCP secrets contribute KEY NAMES only via `custom.credential_keys`.
- `session_summary_seed` feeds the Owner's narrative flow: the imported agent
  self-summarizes it into a Narrative via its own `create_narrative` tool
  (not a bulk raw-history import).
- `FrameworkDetection` is the lighter `detect`-only result (framework + path +
  confidence + matched signals).
