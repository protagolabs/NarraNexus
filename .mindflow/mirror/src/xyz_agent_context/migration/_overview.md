---
code_file: src/xyz_agent_context/migration/
stub: false
last_verified: 2026-07-21
---

# migration/ — Agent Migration Scanner (detect + extract)

The **first build** of the Agent Migration feature (import an agent from
Claude Code / Hermes / OpenClaw / Codex into NarraNexus). This package is the
**Scanner**: it reads the user's LOCAL filesystem and produces the framework-
agnostic standardized JSON (`schema/migration_schema.py`). It **never writes**
to NarraNexus and **never extracts non-MCP secrets** — the map+write half is
done later by the Migration Skill (MCP tools) / Import Button (API).

Modeled on Hermes `import-agent` / `claw migrate` (Nous Research): same
scan-source-dirs → detect-framework → extract-by-file pattern.

## Pieces
- `detector.py` — signal-driven framework detection (`detect_all`, `classify_path`).
- `extractors.py` — per-framework file→dimension extraction (best-effort, never raises).
- `scanner.py` — public `detect()` / `scan()` orchestration → StandardizedAgentImport.

## Local-only
Reached via `backend/routes/migrate.py` (`/api/migrate/*`), which is **disabled
on cloud** (no user filesystem there). In local/desktop mode the backend +
executor run on the user's machine, so stdio-MCP servers are even coherent to
import (v1.1 wiring).

## Design
`reference/self_notebook/specs/2026-07-21-agent-migration-tech-design.md` —
holds the gaps (General Memory has no write tool → new `memory_retain` needed;
NarraNexus MCP is URL/headers only; narrative = agent self-summarize) and the
phasing (v1.0 scanner + skill flow; v1.1 Import Button + url-MCP; v2.0 stdio-MCP).
