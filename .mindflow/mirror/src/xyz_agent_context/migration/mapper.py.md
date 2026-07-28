---
code_file: src/xyz_agent_context/migration/mapper.py
stub: false
last_verified: 2026-07-21
---

## Why it exists

The **convert** step: `build_plan(StandardizedAgentImport) -> MigrationPlan`
turns the scanner's framework-agnostic JSON into the concrete write operations a
consumer executes. Pure + side-effect-free so it is unit-testable and shared by
both consumers (Import Button backend, Migration Skill) — they build the same
plan, then execute it their own way.

## The plan

- `awareness_markdown` ← `agent.system_prompt` (Owner: instructions → Awareness).
- `memory` ← `memory[]` (written via the new `memory_retain` MCP tool).
- `skill_names` ← `skills[]` (name-matched against the Skill Marketplace).
- `mcp_url_servers` vs `mcp_stdio_servers`: url is importable now (mcp API);
  stdio is captured but deferred to local-mode wiring (v1.1) — surfaced so the
  user sees it.
- `narrative_instruction` ← `session_summary_seed`: the prompt the agent runs to
  self-author a Narrative via `create_narrative` (not a bulk history import).
- `warnings`: plaintext-secret fields, stdio-not-imported, unmapped files,
  credential keys not imported, custom-framework caution — for the preview UI.
