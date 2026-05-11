---
code_file: backend/routes/agents_artifacts.py
last_verified: 2026-05-09
stub: false
---

## 2026-05-09 hardening — C1 ownership, C6 path confinement, I1 delete order

**C1 — JWT ownership check on all 5 handlers.**
A `_verify_agent_ownership(request, agent_id)` helper was added (mirrors the pattern
from `lark.py`). It is a no-op in local mode (no `request.state.user_id`); in cloud mode
it looks up `agents.created_by` and raises HTTP 403 if it does not match the JWT user.
All 5 route handlers (`list_artifacts`, `get_artifact`, `get_raw`, `patch_artifact`,
`delete_artifact`) now accept `request: Request` as their first parameter and call this
helper before any other logic.

**C6 — Path-confinement in `get_raw`.**
`os.path.join(base, match.file_path)` is replaced by `os.path.realpath(join(...))`,
which is then checked against `os.path.realpath(settings.base_working_path)`. If the
resolved path escapes the workspace root (e.g., a DB-injected `../../etc/passwd`),
the handler returns HTTP 404 and logs a warning. This is a defence-in-depth guard —
the primary prevention is in `artifact_runner.py` which writes only paths inside
the workspace.

**I1 — Delete order flipped: folder first, then DB row.**
`delete_artifact` now removes the on-disk folder *before* `repo.delete()`. If `shutil.rmtree`
raises `OSError`, the endpoint returns HTTP 500 and the DB row is preserved
(both-or-neither contract). `ignore_errors=True` was dropped to surface real failures.
If the folder is already absent, the `os.path.isdir` guard means no error is raised.

## 2026-05-08-r3 simplification — ArtifactEventBus removed; C1.5 unpin guard added

The `get_artifact_event_bus().publish(...)` calls added on 2026-05-08 have been
removed from both `patch_artifact` and `delete_artifact`. The in-process event bus
was dropped because it lived in the MCP server process, not the FastAPI process,
so `publish()` in `artifact_runner.py` never reached the `/ws/artifacts/{agent_id}`
subscribers. The `from xyz_agent_context.utils.artifact_events import get_artifact_event_bus`
import was removed accordingly.

A new **C1.5 unpin guard** was added to `patch_artifact`: if `body.pinned is False`
and the existing artifact's `original_session_id` is `None` and the artifact is
currently pinned, the handler raises `HTTP 400`. This covers agent-created artifacts
that were auto-pinned at creation time (C1 fix in `artifact_runner.py`); their
`original_session_id` is null because they never had a session to restore. Unpinning
such an artifact would silently drop it into limbo (visible neither in `list_by_session`
nor `list_pinned`). The 400 message instructs the caller to use DELETE instead.

## 2026-05-08 addition — ArtifactEventBus integration (superseded, removed in r3)

# agents_artifacts.py

## Why it exists

HTTP boundary for the artifact lifecycle: list, retrieve detail + versions,
serve raw content, patch metadata, and hard-delete. Artifacts are
agent-emitted structured outputs (HTML apps, ECharts JSON, CSV, Markdown,
images, PDF) that the frontend renders as tabbed panels.

Kept separate from `agents_files.py` (flat workspace tool-input files) and
`agents_attachments.py` (user-uploaded chat attachments) because artifacts
have a distinct storage shape (versioned rows in `instance_artifact_versions`
+ an `instance_artifacts` metadata row) and a different access pattern (never
browsed by raw filename — always accessed by `artifact_id` + `version`).

## Upstream / Downstream

Upstream:
- Frontend `ArtifactTab.tsx` calls `GET /{agent_id}/artifacts?scope=session&session_id=…`
  to populate the tab list after each agent turn.
- Frontend calls `GET /{agent_id}/artifacts/{aid}/v{n}/raw` inside an
  `<iframe sandbox>` to render HTML/ECharts/image content.
- Frontend calls `PATCH` / `DELETE` for pin/title edits and tab dismissal.

Downstream:
- `ArtifactRepository` (repository layer) for all DB reads/writes.
- `settings.base_working_path` as root for on-disk file resolution and
  folder deletion.
- `shutil.rmtree(…, ignore_errors=True)` for folder removal on DELETE —
  never raises even on partial-missing trees.

Mounted under `/api/agents` via `backend/routes/agents.py` (Task 8 wiring).

## Design decisions

**Kind-specific Content-Security-Policy on `/raw`.**
Each MIME kind maps to a hand-crafted CSP that allows only the minimum
needed for that content type to render:
- HTML: `script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:`.
  `script-src 'unsafe-inline'` is REQUIRED — without it, `default-src 'none'`
  would block every inline `<script>` block and every inline event handler
  (`onclick="..."`), making interactive HTML artifacts inert. With it,
  inline JS executes inside the sandboxed iframe, but external script loads
  (`<script src=...>`), `fetch()`, `XMLHttpRequest`, and `WebSocket` still
  fall back to `default-src 'none'` and are blocked — the document can run
  arbitrary JS but cannot phone home. Inline styles allowed for ergonomic
  markup; data:/blob: images allowed for embedded thumbnails.
- JSON / CSV / Markdown: `default-src 'none'` — pure data, no active content.
- Image / PDF: `img-src 'self'` / `object-src 'self'` — self-reference only.

`SAFE_HEADERS` (nosniff, SAMEORIGIN, no-referrer, CORP same-origin) are
added to every `/raw` response alongside the CSP.

**`agent_id` ownership check on every mutating and read endpoint.**
Fetching by `artifact_id` alone and then comparing `art.agent_id != agent_id`
prevents cross-agent reads/mutations even if a caller guesses a valid ID.
Returns 404 (not 403) to avoid leaking existence information.

**Hard delete: DB cascade then `shutil.rmtree`.**
`ArtifactRepository.delete()` removes versions first, then the artifact row
(inside a transaction). The on-disk folder is removed after the DB commit;
`ignore_errors=True` means a partial folder on disk does not cause a 500.

**PATCH uses two separate DB helpers for `pinned` vs `title`.**
`set_pinned(True)` needs a raw SQL UPDATE to also NULL out `session_id` in
one atomic statement. The title-only path uses `db.update()` which is
sufficient (no NULL needed). Keeping them separate avoids a partial-update
footgun where both fields must be checked before issuing a combined query.

## Gotchas

- `get_raw` constructs the absolute path as
  `os.path.join(settings.base_working_path, match.file_path)`.
  The `file_path` stored in `instance_artifact_versions` is always relative
  to `base_working_path` (written that way by `artifact_runner.py`). Any
  drift between the stored relative path and the workspace root will cause
  a 410 "artifact file missing on disk".
- The router is currently not wired into `backend/main.py` (Task 8 does that).
  Tests mount it directly on a fresh FastAPI app for isolation.
- Lint: `logger` is imported but only used in `get_raw` (410 path) and
  `delete_artifact` (folder-deleted log). This is intentional — future
  observability hooks should log entry/exit for all endpoints.

## New-joiner traps

- Authentication is handled by FastAPI middleware, NOT this file. Do not add
  JWT extraction here; it flows in from `backend/auth.py`.
- The `settings` object imported at module level is patched in tests via
  `monkeypatch.setattr(artifacts_mod, "settings", …)`. Do not re-import
  `settings` inside a function body — the module-level binding must remain the
  single reference so the monkeypatch takes effect.
