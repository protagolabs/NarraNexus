---
code_file: src/xyz_agent_context/artifact/artifact_service.py
last_verified: 2026-07-21
stub: false
---

# artifact_service.py — public protocol layer (Service + Bridge)

## Why it exists

Single entry point for artifact **domain operations**: `register` (pointer
registration, ex-`artifact_runner`), `heal` (broken-pointer recovery,
extracted from the agents_artifacts route handler), and `resolve_raw_file`
(raw-content path resolution, extracted from the artifacts_public route
handler). Concrete logic lives in `_artifact_impl/`; this class is the bridge,
mirroring the NarrativeService / ModuleService pattern.

## Deliberate scope boundary

Plain CRUD (list / get / delete / set_pinned / update_title) intentionally
stays on `ArtifactRepository` — the service is NOT a pass-through facade over
every repository method. Rule of thumb: if the operation has rules beyond a
single table write, it belongs here; otherwise call the repository.

## Upstream / Downstream

- Constructed per-request with an `AsyncDatabaseClient` (stateless besides the
  repo handle — cheap, matches how routes and the MCP tool get their client).
- Called by: `artifact_tool.py` (MCP), `agents_artifacts.py` (manual register
  + heal), `artifacts_public.py` (raw serving), `bootstrap/profiles.py`
  (welcome artifact).
- All failures raise the `ArtifactError` hierarchy (`.code` → HTTP status), so
  MCP and HTTP callers convert uniformly with a single except clause.
