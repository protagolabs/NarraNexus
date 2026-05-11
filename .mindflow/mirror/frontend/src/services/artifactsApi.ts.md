---
code_file: frontend/src/services/artifactsApi.ts
last_verified: 2026-05-08
stub: false
---

# services/artifactsApi.ts — Artifacts REST API client

## Why it exists

Centralizes all `fetch` calls targeting `/api/agents/{agentId}/artifacts` so nothing else in the frontend constructs those URLs by hand. Every operation (list, detail, pin toggle, delete) has a typed wrapper that throws on non-OK responses rather than returning an ambiguous `null`.

## Upstream / Downstream

Depends on `@/types/artifact` for `Artifact` and `ArtifactWithVersions`. Has no React or Zustand dependency — it is a pure async module that can be called from anywhere.

Primary consumer is `stores/artifactStore.ts`, which calls these methods inside Zustand actions. The store owns error handling above this layer; `artifactsApi` itself throws, letting callers decide whether to show a toast, retry, or silently swallow (as the WS handler does for race-condition deletes).

## Design decisions

**`base()` helper instead of a hardcoded string.** The base URL is constructed once per call rather than stored as a module-level constant, because `agentId` varies per call. This avoids accidental cross-agent leakage if the helper were reused with a stale closure.

**`scope=session` vs `scope=pinned` query params.** The backend serves both categories through the same endpoint with a `scope` discriminator, matching the REST pattern used elsewhere in the codebase (`GET /api/agents/{id}/artifacts?scope=...`). This keeps the URL surface minimal — no `/pinned` sub-resource.

**No auth headers.** The project-wide convention is that the session cookie / JWT is included automatically by the browser (cookie) or by a Vite proxy forwarding the `Authorization` header. This service does not inject tokens itself.

**`remove` returns `Promise<void>`.** A successful DELETE has no meaningful body. Consumers only need to know it succeeded or threw.

## Gotchas

- `listSession` + `listPinned` are called in parallel by `artifactStore.loadForSession`. Neither call is debounced — if called rapidly (e.g., tab switching), they will fire multiple concurrent requests. The store's `set()` call is the last-write-wins reconciliation point.
- `setPinned` sends a PATCH. If the backend changes to PUT for the full resource, update here too — the store's `upsert` call after the PATCH assumes the response shape matches `Artifact`.
