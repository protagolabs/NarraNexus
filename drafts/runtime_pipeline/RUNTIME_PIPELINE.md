# Agent Runtime Pipeline — Complete Execution Flow

> Last updated: 2026-03-24

## Overview

When a user sends a message via WebSocket, the `AgentRuntime.run()` async generator executes a multi-step pipeline. The WebSocket handler (`backend/routes/websocket.py:122`) iterates the generator — the connection stays open until the generator is exhausted.

**As of 2026-03-24**, Steps 5-6 (hooks + callbacks) run in background via `asyncio.create_task()`, so the WebSocket closes right after Step 4 completes. The sidebar shows 5 steps (0-4 + instant Step 5 "background" indicator).

```
User Message (WebSocket)
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│  Step 0    Initialize                              ~100ms    │
│  Step 1    Select Narrative                        ~500ms    │
│  Step 1.5  Init Markdown                           ~50ms     │
│  Step 2    Load Modules (LLM decision)             ~500ms    │
│  Step 2.5  Sync Instances                          ~100ms    │
│            ─── user sees "loading" progress ───              │
├──────────────────────────────────────────────────────────────┤
│  Step 3    Agent Loop (Claude CLI)   STREAMING     ~1-30s    │
│            ─── user sees response tokens ───                 │
├──────────────────────────────────────────────────────────────┤
│  Step 4    Persist Results           BLOCKING      ~0.5-1s   │
│            ─── user waits briefly ───                        │
├──────────────────────────────────────────────────────────────┤
│  Step 5    Post-processing           INSTANT       ~0ms      │
│            (dispatches hooks to background)                   │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
WebSocket sends "complete" → connection closes → UI unlocks
    │
    ▼  (background, non-blocking)
┌──────────────────────────────────────────────────────────────┐
│  [BG] Execute Hooks (all module after-hooks in parallel)     │
│  [BG] Process Callbacks (fire-and-forget)                    │
│       ~2-10s, logged as [BG] Steps 5-6 completed            │
└──────────────────────────────────────────────────────────────┘
```

### Timing logs in WebSocket handler
```
Agent execution completed — total=X.Xs, post-stream (step 4)=Y.Ys
[BG] Steps 5-6 dispatched to background for {agent_id}
[BG] Steps 5-6 completed for {agent_id} in Z.Zs
```

---

## Step-by-Step Detail

### Step 0 — Initialize
**File:** `agent_runtime/_agent_runtime_steps/step_0_initialize.py`
**Blocks user:** Yes
**Yields:** ProgressMessage

| Sub-step | What it does |
|----------|-------------|
| 0.1 | Load agent config from DB |
| 0.2 | Init ModuleService |
| 0.3 | Create Event record in DB |
| 0.4 | Get or create Session |
| 0.5 | Load Agent Awareness text |

---

### Step 1 — Select Narrative
**File:** `agent_runtime/_agent_runtime_steps/step_1_select_narrative.py`
**Blocks user:** Yes
**Yields:** ProgressMessage

- Detect narrative ownership (user vs participant)
- Vector search for matching narratives (embedding call)
- Create new or reuse existing narrative
- Ensure user has a ChatModule instance

---

### Step 1.5 — Init Markdown
**File:** `agent_runtime/_agent_runtime_steps/step_1_5_init_markdown.py`
**Blocks user:** Yes
**Yields:** Nothing (plain `await`, not async generator)

- Read markdown history file from disk
- Parse historical conversations
- Extract instance state

---

### Step 2 — Load Modules & Decide Execution Path
**File:** `agent_runtime/_agent_runtime_steps/step_2_load_modules.py`
**Blocks user:** Yes
**Yields:** ProgressMessage

- LLM call to decide which modules are needed (can be skipped via config)
- Instantiate module objects
- Decide execution path: `AGENT_LOOP` (99%) or `DIRECT_TRIGGER` (1%)
- Start MCP servers for selected modules

---

### Step 2.5 — Sync Instances
**File:** `agent_runtime/_agent_runtime_steps/step_2_5_sync_instances.py`
**Blocks user:** Yes
**Yields:** ProgressMessage

- Update markdown with instance changes
- Sync instance changes to database (add/remove/update)

---

### Step 3 — Agent Loop (STREAMING)
**File:** `agent_runtime/_agent_runtime_steps/step_3_agent_loop.py`
**Blocks user:** Partially — streams tokens in real-time
**Yields:** ProgressMessage + **AgentTextDelta** (streamed) + PathExecutionResult (captured internally)

| Sub-step | What it does |
|----------|-------------|
| 3.1 | Init ContextRuntime |
| 3.2 | Build context — runs `hook_data_gathering` on all modules (injects Matrix info, social network, awareness, etc.) |
| 3.3 | Extract messages + MCP URLs |
| 3.4 | **Run Claude CLI agent loop with streaming** — each token yielded to WebSocket immediately (line 161-171) |
| 3.5 | Finalize output, build PathExecutionResult |

The `PathExecutionResult` is NOT sent to the client — it's captured in `ctx.execution_result` for Steps 4-5.

---

### Step 4 — Persist Results (BLOCKING)
**File:** `agent_runtime/_agent_runtime_steps/step_4_persist_results.py`
**Blocks user:** Yes
**Yields:** ProgressMessage

User already has the response but WebSocket is still open. All sub-steps are `await`ed:

| Sub-step | What it does | I/O type | Latency |
|----------|-------------|----------|---------|
| 4.1 Record Trajectory | Write execution trace (user input, instances, reasoning, tool calls) to trajectory file | File I/O + DB | ~100-300ms |
| 4.2 Update Markdown Stats | Compute round count, tool calls, most-used module; write to markdown file | File I/O | ~10-50ms |
| 4.3 Update Event | Build EventLogEntry list from execution steps; write Event to DB; sync `final_output` to in-memory Event (needed by Step 5 hooks) | DB write | ~50-100ms |
| 4.4 Update Narratives | For each narrative in `narrative_list`: link Event to narrative, update dynamic_summary. Main narrative gets full LLM update + embedding refresh. Auxiliary narratives get basic update only. Default narrative only gets event_id appended. | DB write + LLM (for main narrative) + embedding API | ~100-500ms |
| 4.5 Update Session | Save `last_response` to Session record | DB write | ~50ms |
| 4.6 Record LLM Cost | Write token usage + cost to `cost_records` table | DB write | ~10-50ms |

**Total: ~300-1100ms**

**Why Step 4 must be synchronous:** Step 4.3 sets `ctx.event.final_output` which Step 5 hooks depend on. If we move Step 4 to background, hooks won't have the response text to analyze.

---

### Step 5 — Post-processing (BACKGROUND, non-blocking)
**File:** `agent_runtime/_agent_runtime_steps/step_5_execute_hooks.py`
**Blocks user:** No (dispatched via `asyncio.create_task()` in `agent_runtime.py`)
**Sidebar:** Shows "Post-processing (background) ✓" instantly

As of 2026-03-24, Steps 5 + 6 are combined into a single background task. The runtime yields an instant "completed" ProgressMessage for Step 5, then spawns the actual work as a background coroutine. The WebSocket closes immediately.

The background task calls `hook_after_event_execution` on every active module. Modules run **in parallel** via `asyncio.gather()`.

#### Per-Module Hook Detail:

| Module | What it does | LLM calls | API calls | DB writes | Latency |
|--------|-------------|-----------|-----------|-----------|---------|
| **ChatModule** | Save input+output messages to chat history. Inject bootstrap greeting on first turn. Update module status report. | No | No | 2 DB writes | 5-100ms |
| **SocialNetworkModule** | Auto-extract entity info from conversation. LLM summarizes identity/role keywords. Update entity description, embedding, interaction stats. Optionally infer communication persona (every 10 turns). | 1-3 LLM calls | 1 embedding API | 3-5 DB writes | **2-5s** |
| **JobModule** | On JOB trigger: LLM analyzes execution to determine job completion. On CHAT trigger: check active ONGOING jobs against end conditions (1 LLM call per job). Update job status, process history. | 1-N LLM calls | No | Multiple DB writes | **1-10s** |
| **MemoryModule** | Write conversation event to EverMemOS (external memory service). No-op if EverMemOS disabled. | No | 1 HTTP call to EverMemOS | No | 100ms-1s |
| **MatrixModule** | Only runs on Matrix-triggered events. Mark replied rooms as read. | No | 1 Matrix API call per room | No | 50-500ms |
| **AwarenessModule** | No hook | — | — | — | 0 |
| **BasicInfoModule** | No hook | — | — | — | 0 |
| **SkillModule** | No hook | — | — | — | 0 |

**Total (parallel): max of all = 2-10s**, dominated by SocialNetworkModule and JobModule LLM calls. Runs entirely in background — user is not affected.

After hooks complete, **callback results** are processed (dependency chains, newly activated instances spawned as further background tasks).

**Background task safety:**
- Modules use the global shared DB client from `db_factory.get_db_client()` (not the runtime's own client), so DB access survives after `AgentRuntime.__aexit__` closes its connection.
- `ctx.event.final_output` is already persisted by Step 4.3 before the background task starts.
- `clear_cost_context()` is called in the background task's `finally` block.
- Failures are logged as `[BG] Steps 5-6 failed for {agent_id}` but do not affect the user.

---

## Timing Breakdown (Typical Request)

```
Phase                    Duration    User sees
────────────────────────────────────────────────────────────
Steps 0-2.5              ~1.2s       Progress indicators
Step 3 (streaming)       ~3-15s      Response tokens (real-time)
Step 4 (persist)         ~0.5-1s     Brief wait (screen locked)
Step 5 (bg dispatch)     ~0ms        "Post-processing ✓" (instant)
────────────────────────────────────────────────────────────
WebSocket closes         ~4.7-17s    UI unlocks
User wait after response ~0.5-1s     (Step 4 only)

[Background]             ~2-10s      User doesn't see this
Steps 5-6 (hooks+cbs)               Logged as [BG] completed
```

---

## Historical Context

**Reported issue (pre 2026-03-24):** "最后async 还是会锁住屏幕, 需要把 最后的 hook 转化成真异步进行的工作。"

**Fix applied:** Moved Steps 5-6 to `asyncio.create_task()` in `agent_runtime.py`. The user previously waited 2-11s after seeing the response; now they wait only ~0.5-1s (Step 4 persistence).

The WebSocket handler in `websocket.py:122-129` does:
```python
async for message in runtime.run(...):
    await websocket.send_json(message_dict)
# ← only reaches here after ALL steps complete
await websocket.send_json({"type": "complete"})
```

The `async for` loop doesn't exit until the runtime generator is exhausted (all 6 steps done). Even though the user's response was streamed during Step 3, the "complete" signal and connection close are delayed by Steps 4-5.

**Impact:** UI shows spinner/loading state for 2-11 seconds after the agent's full response is visible. The user cannot send another message until the connection closes.

---

## Future Optimization Options

If Step 4 (~0.5-1s) is still too slow, consider:

- **Move Step 4 to background too** — yield "complete" right after Step 3. Risk: if persistence fails, data is lost silently. Mitigation: add error logging + retry queue.
- **Split Step 4** — keep 4.3 (Event update) synchronous (needed by hooks), move 4.1, 4.2, 4.4, 4.5, 4.6 to background. Best balance of safety and speed.

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/routes/websocket.py` | WebSocket handler, iterates runtime.run(), timing logs |
| `src/xyz_agent_context/agent_runtime/agent_runtime.py` | Main orchestrator, run() method, background task dispatch |
| `src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_4_persist_results.py` | Persistence (blocking, synchronous) |
| `src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_5_execute_hooks.py` | Hook execution (runs in background task) |
| `src/xyz_agent_context/module/hook_manager.py` | Parallel hook dispatch via asyncio.gather() |
| `src/xyz_agent_context/module/social_network_module/social_network_module.py:270` | Slowest hook (1-3 LLM calls) |
| `src/xyz_agent_context/module/job_module/job_module.py:511` | Second slowest hook (1-N LLM calls per active job) |
| `frontend/src/stores/chatStore.ts` | TOTAL_PIPELINE_STEPS = 5 |
