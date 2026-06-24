---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_5_execute_hooks.py
last_verified: 2026-06-24
stub: false
---

## 2026-06-24 — distrust policy short-circuit (IM distrust v1)

`step_5_execute_hooks` now early-returns at the top when
`ctx.policy.skip_after_execution_hooks` (the static-visitor/distrust profile) — it
yields one COMPLETED ProgressMessage and returns BEFORE building params or calling
any hook, so no module after-execution hook runs and no callback_results is yielded
(the background driver's Step 6 is guarded by `if hook_callback_results`, so it
no-ops too). Purpose: an untrusted external IM visitor turn must not mutate the
owner's persistent state (narrative / memory / chat history). The matching
synchronous phase — `hook_persist_turn` after Step 4 in `agent_runtime.run()` — is
gated by the SAME flag (otherwise the ChatModule conversation row would still be
written into the owner's scope). See [[runtime_policy.py]].

## 2026-05-20 — extracted `build_after_execution_params(ctx)`

The current-instance resolution + HookAfterExecutionParams construction was lifted
out of `step_5_execute_hooks` into a module-level `build_after_execution_params(ctx)`
so the new SYNCHRONOUS persistence phase ([[agent_runtime.py]] Step 4.6 →
[[hook_manager.py]] `hook_persist_turn`) builds identical params without duplicating
the resolution logic. Step 5 (background) now just calls it. Pure read over ctx.

# step_5_execute_hooks.py — Pipeline Step 5: Execute Module Post-turn Hooks

## Why It Exists

After the turn is persisted (Step 4), each active Module gets a chance to run its `hook_after_event_execution` callback. These hooks handle Module-specific post-processing: saving chat messages to ChatModule, triggering Job scheduling in JobModule, updating social graph data, etc. Running hooks after persistence ensures they operate on committed data and don't block the WebSocket response.

## Upstream / Downstream

**Called by:** `agent_runtime.py` — dispatched as a background `asyncio.Task` after Step 4 completes, so the WebSocket can close while hooks run

**Calls:**
- `hook_manager.run_hooks()` — iterates all active Module instances and calls `hook_after_event_execution` on each
- Each Module's `hook_after_event_execution(params: HookAfterExecutionParams)` implementation

**Produces:**
- `callback_results` dict — returned via the final `yield` in the generator; collected by the background task wrapper in `agent_runtime.py`
- Side effects in DB (per-Module)

## Key Design Decisions

### Dispatched as Background Task
Steps 5 and 6 are pushed to `asyncio.create_task()` after Step 4. This means the WebSocket connection can close and the HTTP response can be sent while hooks run in the background. The client does not wait for hooks.

This is intentional: hooks can be slow (e.g., JobModule scheduling requires LLM calls). Blocking the user's response on hook completion would degrade perceived latency.

### current_instance Resolution by working_source
The `current_instance` parameter passed to `HookAfterExecutionParams` is determined differently based on `ctx.working_source`:
- **CHAT**: uses `ctx.user_chat_instances[narrative_id]` — the per-user ChatModule instance established in Step 1
- **JOB**: uses `ctx.job_instance_id` — the specific JobModule instance that triggered the turn
- **Other**: falls back to `None`

This distinction matters because hooks need to know "which instance owns this turn's data" to route their writes correctly.

### callback_results Return via Final Yield
The generator's final `yield` carries `callback_results` as the `details` field of the last `ProgressMessage`. The background task wrapper in `agent_runtime.py` collects this via `async for` and stores it on `ctx`. This is an unusual pattern — it's how the background task communicates results back without a shared mutable reference.

## ContextData Mutations

| Field | What Happens |
|-------|-------------|
| `ctx.callback_results` | Set by the background task wrapper after this generator completes |
| Module-specific DB tables | Each Module's hook writes its own data (chat_messages, job_runs, etc.) |

## Gotchas / Edge Cases

- **Hook failure isolation**: Each hook runs in a try/except. One Module's hook failure does not prevent other Modules' hooks from running. Errors are logged and added to `callback_results` with an error status.
- **Background task lifecycle**: Since this runs as a background task, it may outlive the HTTP request. Do not hold references to request-scoped objects (e.g., WebSocket connection) inside hooks.
- **Ordering not guaranteed**: Hooks run in iteration order of `ctx.active_instances`. If Hook A depends on Hook B's side effects, this is a design smell — hooks should be independent.
- **No cancellation token**: Background tasks (Steps 5–6) do not receive the `CancellationToken`. If the user cancels the turn, hooks still run to completion on the already-persisted data.

## Common New-Developer Mistakes

- Expecting hook results to be available before the WebSocket response: they're in a background task, so the client may not see them until the next turn.
- Adding slow synchronous operations to a hook: all hooks must be async. Blocking the event loop in a hook will delay all other background tasks.
- Forgetting that `ctx.user_chat_instances` may not have an entry for every Narrative — always use `.get()` with a fallback, not direct key access.
