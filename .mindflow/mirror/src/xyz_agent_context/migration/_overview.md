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
- `extractors.py` — per-framework file→dimension extraction (best-effort, never
  raises). Layouts verified against Hermes `agent_import.py` (MIT): Claude MCP is
  in `~/.claude.json` (not `.mcp.json`); Codex reads `config.toml`; OpenClaw
  persona/memory under `workspace/`. Also flags which MCP fields carry secrets
  (`secret_fields`) — secrets hide in args/url too, not just env/headers.
- `scanner.py` — public `detect()` / `scan()` → StandardizedAgentImport. Adds the
  Claude **session** layer (`~/.claude/projects/<encoded-cwd>/*.jsonl` → one
  `MigrationSession` per session) — Hermes ignores sessions; this is our
  differentiator.
- `mapper.py` — the **convert** step: `build_plan(StandardizedAgentImport) →
  MigrationPlan` (awareness / memory / skills / per-session `narratives` /
  url-vs-stdio MCP split / warnings). Pure; both consumers build the same plan.

## Write-path note
`applier.apply_plan` executes the plan directly (no HTTP, no agent loop):
Awareness → `InstanceAwarenessRepository.upsert`; General Memory + imported
session turns → `MemoryEngine.retain`; skills → verbatim copy or Skill
Marketplace install; url-MCP → `MCPRepository.add_mcp`; each session → a Narrative
via `NarrativeService` (summarized by one helper_llm call). No embeddings —
narrative routing is BM25.

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
