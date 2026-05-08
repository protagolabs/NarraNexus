---
code_file: src/xyz_agent_context/schema/artifact_schema.py
last_verified: 2026-05-08-r2
stub: false
---

# artifact_schema.py

## Why it exists

This schema defines the Pydantic shapes for agent-emitted Artifacts — structured outputs (charts, HTML apps, CSV tables, markdown reports, images, PDFs) that an agent produces during a conversation and that the frontend surfaces in a dedicated "Artifacts" tab alongside the chat.

It is the single source of truth that ties together three distinct concerns:
1. **Persistence**: `Artifact` is the metadata row stored in the database; `ArtifactVersion` tracks each content revision.
2. **Tool contract**: `CreateArtifactToolResult` is what the MCP `create_artifact` tool returns to the LLM after writing a file, so the agent can reference the `url` in its reply.
3. **API transport**: `ArtifactWithVersions` is the read-side shape that the REST endpoints serve to the frontend.

## Upstream / Downstream

**Producers:**
- `ArtifactRepository` (repository layer) creates and updates `Artifact` + `ArtifactVersion` rows.
- The MCP `create_artifact` tool (inside whatever Module hosts artifact creation) calls `ArtifactRepository` and returns a `CreateArtifactToolResult` to the LLM.

**Consumers:**
- `backend/routes/artifacts.py` — REST endpoints that list, fetch, pin, and delete artifacts; returns `ArtifactWithVersions` to the frontend.
- `frontend/src/stores/artifactStore.ts` — Zustand store that types its state against `Artifact` (mirrored as a TypeScript interface generated from this schema).
- `frontend/src/components/chat/ArtifactPanel.tsx` — renders the Artifacts tab using `ArtifactWithVersions`.

## Design decisions

**`ArtifactKind` as a `Literal` string, not an Enum.** Literals serialize directly to their string value in JSON (no `.value` indirection), are instantly readable in DB rows, and compose cleanly with `Annotated` validators. Extending the set means adding one string to the Literal — no migration of existing rows, no registry change.

**7 predefined kinds, no "other".** The whitelist enforces that every artifact is renderable by the frontend. An "other/binary" escape hatch would let the agent emit content the UI cannot display, breaking the viewer guarantee. New kinds require a matching frontend renderer before they can be added here.

**`session_id` nullable ⇔ pinned scope.** When `session_id` is `None`, the artifact is agent-scoped ("pinned") and survives session cleanup. When set, it is session-scoped and can be garbage-collected with the session. The `pinned` boolean is a user-facing flag that promotes a session artifact to agent scope (sets `session_id = NULL`). This avoids a separate `pinned_artifacts` table.

**Versioning via `ArtifactVersion` rows, not file mutation.** Each `create_artifact` or `update_artifact` tool call appends a new `ArtifactVersion` row and bumps `Artifact.latest_version`. The frontend can offer a version history dropdown without any additional API. File paths are immutable once written, so older versions remain accessible.

**`file_path` is relative to `settings.base_working_path`.** Absolute paths would break when the working directory changes (container restart, environment migration). The repository layer resolves paths at read time by joining with the runtime base path.

**`artifact_id` uses the `art_` prefix + 8 random chars** (e.g., `art_a1b2c3d4`), matching the project-wide ID generation convention (铁律 #naming).

## Gotchas

- `ArtifactVersion.id` is the DB auto-increment integer; `artifact_id` is the business key. Never use `id` for cross-service references.
- `Artifact` in `schema/__init__.py` is exported as `Artifact` (the canonical name). The A2A protocol model was renamed to `A2AArtifact` to free this name (2026-05-08). Internal code within `artifact_schema.py` and `ArtifactRepository` uses `Artifact` directly — no alias needed anywhere.
- `size_bytes` on `ArtifactVersion` must be populated at write time by the tool implementation. The repository does not stat the file; it trusts the caller.

## New-joiner traps

- Do not confuse `Artifact` (this file) with `A2AArtifact` from `a2a_schema` — the A2A one represents a task output chunk in the Google A2A protocol and has a completely different shape.
- `ArtifactKind` is a `Literal` type alias, not a class. `isinstance(x, ArtifactKind)` does not work — use `x in get_args(ArtifactKind)` if runtime membership checking is needed.
- The `pinned` field is the user intent; `session_id is None` is the DB-level representation. Always keep them in sync when writing via the repository.
- `original_session_id` stores the `session_id` value captured at pin time so that `set_pinned(False)` can restore it. `None` on rows that were never pinned, or on legacy rows pinned before this column was added.
