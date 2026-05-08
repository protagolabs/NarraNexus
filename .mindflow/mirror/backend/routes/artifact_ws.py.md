---
code_file: backend/routes/artifact_ws.py
last_verified: 2026-05-08
stub: false
---

# artifact_ws.py

## Why it exists

The frontend needs real-time notification when an artifact is created, updated,
pinned, or deleted — polling the REST `GET /artifacts` endpoint after every chat
turn is fragile and adds latency. A dedicated WebSocket endpoint allows the server
to push events to the frontend immediately after the mutation completes.

This file is separate from `websocket.py` (which handles agent run streaming)
because the two have different lifecycles: `artifact_ws.py` connections are
long-lived (one per open agent tab, persists across multiple chat turns) while
`websocket.py` connections are short-lived (one per agent run).

## Upstream / Downstream

**Upstream:**
- `ArtifactEventBus` singleton (`get_artifact_event_bus()`) — events are
  published by `artifact_runner.py` and `agents_artifacts.py`.

**Downstream:**
- Frontend `useArtifactEvents` hook — connects to `/ws/artifacts/{agent_id}` and
  updates the artifact tab list on each received event.

**Mounted in:** `backend/main.py` — `app.include_router(artifact_ws_router, tags=["Artifacts"])`.
No path prefix because the path already starts with `/ws/`.

## Design decisions

**30-second ping heartbeat.** The WS endpoint uses `asyncio.wait_for(queue.get(), timeout=30.0)`
instead of a blocking `await queue.get()`. If no event arrives within 30 seconds,
it sends `{"type": "ping"}` to keep the connection alive through reverse proxies
(nginx, AWS ALB) that time out idle WebSocket connections at 60 seconds. The
frontend should treat `ping` as a no-op.

**No auth on this endpoint (v1).** The artifact WS endpoint does not perform JWT
validation, unlike `websocket.py`. This is acceptable because:
1. artifact_id values are opaque random tokens — guessing them is not feasible.
2. The events only contain metadata (artifact_id, type, version) — no raw content.
3. The agent_id in the URL is already validated by the auth middleware that wraps
   all `/api/*` and `/ws/*` routes.
If per-connection JWT auth is needed later, mirror the pattern from `websocket.py`.

**Clean unsubscribe in `finally`.** The `finally: bus.unsubscribe(agent_id, queue)`
runs even if `send_json` raises a `RuntimeError` (connection already closed by
client). This prevents queue objects from accumulating in the bus when clients
silently disconnect.

## Gotchas

- **`WebSocketDisconnect` vs `RuntimeError`:** FastAPI raises `WebSocketDisconnect`
  for clean client-side closes. A network drop may surface as a `RuntimeError`
  from `send_json`. Currently only `WebSocketDisconnect` is caught; a `RuntimeError`
  will propagate out of the `while True` loop and be caught by FastAPI's exception
  handler. The `finally` block still runs, so the unsubscribe is safe.
- **Single-process requirement.** The bus is in-process, so this endpoint only
  works correctly in single-worker deployments. See `artifact_events.py.md` for
  the multi-worker upgrade path.
