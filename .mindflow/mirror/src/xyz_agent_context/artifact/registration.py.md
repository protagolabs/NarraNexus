---
code_file: src/xyz_agent_context/artifact/registration.py
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — promoted to shared xyz_agent_context/artifact/ (was common_tools_module/_common_tools_impl/artifact_runner.py)

The pointer-model `register_artifact` core was **moved out of a Module's
private impl** into shared infrastructure at `xyz_agent_context/artifact/`.
Nothing about its behaviour changed — the move is purely about **who is
allowed to call it**. It is not a Module feature; it is infrastructure that
several unrelated callers need:

- [[artifact_tool]] — the common_tools `register_artifact` MCP tool (now a thin
  wrapper).
- [[_office_mcp_tools]] — OfficeModule's `office_render`.
- [[agents_artifacts]] — the backend REST manual-register + heal endpoints.
- bootstrap welcome artifacts ([[profiles]]).

Before the move, any of these reaching for it meant cross-importing
`common_tools_module`'s private `_common_tools_impl/` — a binding-rule-#3
violation (Modules must not depend on each other's internals; shared logic
goes through a shared/Service layer). Promoting the runner here dissolves that
coupling: every caller imports `from xyz_agent_context.artifact import
registration` instead.

`ALL_KINDS` now also accepts the **three Office OOXML kinds** (…wordprocessingml.document,
…spreadsheetml.sheet, …presentationml.presentation) so `office_render` can
register a .docx/.xlsx/.pptx entry — see [[artifact_schema]] for the shared
literal, and [[agents_artifacts]] `_KIND_EXTENSIONS` for the heal-by-extension
counterpart. The `MAX_ARTIFACT_BYTES` cap, the size-accounting split, and the
path-escape checks are unchanged from the old `artifact_runner`.

## Why it exists

`register_artifact` registers a **pointer** to an entry file the agent already
wrote inside its own workspace. It never copies, moves, or writes content — it
validates the path, computes the artifact's size, and writes (or updates) one
`instance_artifacts` row.

The core mental model (carried over from the old artifact_runner): an artifact
= **an entry file + the directory it lives in** (the "artifact root"). The
backend serves that whole root directory, so a multi-file HTML app can
reference sibling assets (css/js/json/images). When the entry sits directly in
the workspace root, the route degrades to serving only the entry (single-file
mode) so the agent's other files are never exposed.

## Upstream / Downstream

**Called by:** [[artifact_tool]], [[_office_mcp_tools]] (`office_render`),
[[agents_artifacts]] (manual register + heal), and the bootstrap welcome-artifact
flow ([[profiles]] `_create_welcome_artifact`).

**Depends on:** `ArtifactRepository` (DB access), `settings.base_working_path`
(workspace root resolution), `utils.workspace_paths.agent_workspace_path`.

Raises a structured exception hierarchy (`ArtifactError` + subclasses
`ArtifactTooLarge`, `ArtifactNotFound`, `ArtifactKindMismatch`,
`ArtifactPathEscape`) with a `.code` mapping to an HTTP status, so each caller
(MCP wrapper / REST route) can convert them into caller-readable errors.

## Design decisions

**Pointer model — no filesystem writes.** The DB `file_path` is the entry file
relative to `base_working_path`; the file stays where the agent wrote it. This
is what makes re-register a cheap "refresh signal" (the agent keeps editing the
same workspace file and calls again with `target_artifact_id`).

**Single-file mode at the workspace root.** If the entry sits directly in the
workspace, `size_bytes` accounts for only the entry file, and the raw route
serves only that entry — sub-path requests 404. Multi-file artifacts must live
in a dedicated subdirectory so their siblings resolve and are size-accounted.

**`MAX_ARTIFACT_BYTES = 25 MB` is the ONLY limit.** A per-artifact recursive
size cap on the artifact root — a sanity guard against a single runaway
artifact. There is **no per-user count or aggregate-byte quota**; the old
deploy-mode-aware quota (50 local / 10 cloud + 100 MB total) was removed in
v1.7.0. Users may register any number of artifacts.

**`realpath` path-escape check.** The resolved entry must start with
`workspace + os.sep` — symlink / `..` escapes out of the agent workspace are
rejected before any DB write.

**No session context → agent-scoped (pinned).** LLM-driven calls cannot know a
`session_id`; when it is `None` the row is created `pinned=True` so it surfaces
via `list_pinned` rather than being orphaned with `session_id=NULL` + unpinned.

## Gotchas

- `kind` is typed `ArtifactKind` here but arrives as a bare `str` from the MCP
  schema — callers pass `# type: ignore[arg-type]`; runtime validation against
  `ALL_KINDS` is here.
- `size_bytes` is UI/debug only under the no-quota model; nothing enforces it
  except the `MAX_ARTIFACT_BYTES` sanity cap.
- The three Office kinds register the **original** office file as the entry
  pointer (so "download original" grabs the real .docx/.xlsx/.pptx). The HTML
  preview the panel renders is a *sibling* of that entry, generated separately
  by [[officecli_client]] — `registration` itself knows nothing about previews.
