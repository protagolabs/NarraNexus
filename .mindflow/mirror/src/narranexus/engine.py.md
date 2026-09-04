---
code_file: src/narranexus/engine.py
last_verified: 2026-09-04
stub: false
---

# engine.py — the headless Engine

`Engine.load(dist, registries, cloud, host_version, store, bus)` resolves a distribution (path/file/resolution/None), registers the builtin contributions when the registries are empty, runs the same `hosts.boot.boot("backend", distribution=...)` the backend host uses and marks it healthy; a second boot of frozen registries is refused. `run_turn(agent_id, user_id, input, ...)` streams `AgentRuntime.run` messages with the engine's registries and db; `agents(user_id)` = own + public agents via `AgentRepository.find`; `events()` = the `EventBus`; `use_db()` borrows a client (not closed by `close()`); `close()` releases an owned db. `run(coro)` is a script convenience.
