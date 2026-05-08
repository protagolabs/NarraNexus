---
code_file: src/xyz_agent_context/utils/artifact_events.py
last_verified: 2026-05-08
stub: false
---

# artifact_events.py

## Why it exists

The spec (§3.5) requires four explicit WebSocket event types: `artifact.created`,
`artifact.updated`, `artifact.pinned`, `artifact.deleted`. These events are
produced by two producers (`artifact_runner.py` in the core package and
`agents_artifacts.py` in the backend routes) and consumed by potentially many
WebSocket connections (one per open browser tab per agent).

A direct call from producer to WebSocket connection is impossible — the route
handler doesn't hold a reference to active WS connections. An in-process pub/sub
bus decouples them: producers call `publish(agent_id, event)`, subscribers (WS
handlers) call `subscribe(agent_id)` and receive events via an `asyncio.Queue`.

## Upstream / Downstream

**Producers (call `publish`):**
- `artifact_runner.py` — emits `artifact.created` / `artifact.updated` after
  `create_text_artifact` and `upload_binary_artifact` complete.
- `backend/routes/agents_artifacts.py` — emits `artifact.pinned` in PATCH and
  `artifact.deleted` in DELETE after the DB mutation commits.

**Consumers (call `subscribe` / `unsubscribe`):**
- `backend/routes/artifact_ws.py` — the WS endpoint subscribes on connect and
  unsubscribes in its `finally` block.

**Global singleton:** `get_artifact_event_bus()` returns a module-level
`ArtifactEventBus` instance. All callers in the same process share this one bus.

## Design decisions

**Per-agent asyncio.Queue set.** The bus holds a `dict[agent_id, set[Queue]]`.
`publish(agent_id, event)` only notifies subscribers for that agent — a frontend
watching agent A does not receive events from agent B.

**Bounded queues with drop-oldest overflow.** Each queue has `maxsize=256`. If a
slow WebSocket client doesn't drain the queue fast enough, `publish()` drops the
oldest event to make room for the latest. This keeps the publisher non-blocking
and ensures recent events are not lost at the expense of old ones. 256 slots gives
a comfortable buffer for bursty agent turns while staying memory-bounded.

**Single-process scope (v1).** This works correctly when deployed as a Tauri
sidecar (single process) or a single uvicorn worker on EC2. Multi-worker
deployments (WEB_CONCURRENCY > 1) would require a Redis-backed bus; that is
explicitly out of scope for v1, matching the existing comment in `main.py`'s
`_warn_if_multi_worker()`.

**`get_artifact_event_bus()` lazy singleton.** Import-time singleton would
interfere with test isolation if tests import the module. Lazy init means the
singleton is created at first use; tests that instantiate `ArtifactEventBus()`
directly bypass the singleton and get clean state.

## Gotchas

- **Module-level singleton is shared across all tests in a session.** Unit tests
  in `test_artifact_events.py` each create their own `ArtifactEventBus()` instance
  (not the singleton) so they are fully isolated. The e2e test in
  `test_agents_artifacts.py` relies on the singleton because it exercises the
  real route handler — this is intentional.
- **`unsubscribe` is idempotent.** Calling it with a queue that was never
  subscribed or was already unsubscribed is a no-op (uses `set.discard`).
- **No persistence.** Events are in-memory only. If the server restarts, any
  queued events are lost. Frontends should re-fetch artifact state via REST
  after reconnecting the WS.
