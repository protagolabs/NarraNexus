---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_3_agent_loop.py
last_verified: 2026-05-12
stub: false
---

## 2026-05-12 — Chat no-reply helper_llm fallback (P0 #3)

After the agent loop completes, step 3 now inspects
`agent_loop_response` for a `send_message_to_user_directly` tool call.
When the turn was chat-triggered (`ctx.working_source == "chat"`) and
no such call exists, step 3 invokes the helper_llm slot via
`OpenAIAgentsSDK.llm_stream` and streams the resulting reply through
`AgentTextDelta` events — exactly the same channel the frontend uses
to render organic LLM stream, so users see the recovered reply in
real time without any frontend change.

After the stream completes, step 3 appends a synthetic
`send_message_to_user_directly` ProgressMessage carrying
`details.reply_via="helper_llm_fallback"`. Downstream:
- `ChatModule._extract_user_visible_response` picks the synthetic call
  up like any organic reply, so the assistant row persists the
  helper-generated text — NOT `io_data.final_output` (reasoning).
- `ChatModule.hook_after_event_execution` lifts the `reply_via` tag
  onto the persisted row's `meta_data.reply_via`.

Why this design (per 5/11 product review):
- `io_data.final_output` is internal reasoning, not speech (project
  iron rule: only `send_message_to_user_directly` counts as speaking).
  The previous "persist final_output directly" shortcut violated this.
- Only chat turns get the fallback. `message_bus` deliberately avoids
  replying to prevent agent-to-agent loops; job/lark/etc. have their
  own reply pathways.
- Streaming the helper_llm output keeps the user experience identical
  to a normal reply (no "blank then long pause then text" UX).

If the helper_llm call itself fails, step 3 logs and lets the
placeholder fall through — the honest record is "no reply" rather
than a silent leak of reasoning.

# step_3_agent_loop.py — Pipeline Step 3 Sub-path: Interactive Agent Loop

## Why It Exists

When `step_3_execute_path.py` routes to the `agent_loop` execution type, this module handles the full sub-pipeline for an interactive LLM-driven turn. It orchestrates sub-steps 3.1 through 3.5: context building, token budget computation, LLM invocation, tool execution, and response processing. This separation keeps the routing layer thin and the agent loop logic focused.

## Upstream / Downstream

**Called by:** `step_3_execute_path.py` — receives `ctx` and yields `ProgressMessage` + `PathExecutionResult`

**Calls:**
- `ContextRuntime.run()` (sub-step 3.2) — builds `ContextData` with all module data injected
- `ClaudeAgentSDK.agent_loop()` (sub-step 3.3) — drives the LLM turn via Claude Code CLI subprocess
- `ResponseProcessor.process()` (sub-step 3.5) — interprets LLM output into `ProcessedResponse`
- `ctx.module_service` — for hook calls between sub-steps

**Produces:** `PathExecutionResult` stored in `ctx.execution_result` by the calling router

## Key Design Decisions

### Sub-step Structure (3.1–3.5)
Each sub-step yields its own `ProgressMessage`. This gives the frontend granular visibility into long-running turns. The sub-step numbers appear in WebSocket progress events, allowing the UI to show "3.3 Calling LLM..." independently.

### skill_env_vars Extraction
`ctx_data.extra_data` is checked for `skill_env_vars` key after ContextRuntime runs. These env vars come from AwarenessModule and are passed directly to the Claude Code CLI subprocess. This is how agent-level tool permissions (e.g., allowed bash commands) propagate to the execution environment.

### Token Budget
Computed before the LLM call from `ctx.event.input_content` length and the loaded context. Budget calculation lives here, not in ContextRuntime, because it depends on the final assembled prompt length.

### Multi-turn History Injection
Chat history is injected into the system prompt (not as native multi-turn messages) because Claude Code CLI's `--system-prompt` flag doesn't support multi-turn natively. The `prompts.py` constants (`CHAT_HISTORY_HEADER`, etc.) wrap the history block.

## ContextData Mutations

| Field | What Happens |
|-------|-------------|
| `ctx_data` | Built fresh by ContextRuntime; not a pre-existing ctx field |
| `ctx.execution_result` | Set by router after this generator yields `PathExecutionResult` |
| `ctx.evermemos_memories` | Read here (cached in step 1); passed to ContextRuntime |

## Gotchas / Edge Cases

- **skill_env_vars missing key**: If AwarenessModule didn't populate `extra_data`, the dict lookup returns `None` gracefully — don't add a default, the SDK handles `None`.
- **ContextRuntime vs agent loop ordering**: ContextRuntime.run() must complete before agent_loop() starts; the context is not streamed incrementally.
- **Sub-step 3.4 (tool execution)**: Tool calls are processed inside `agent_loop()` via MCP — sub-step 3.4 in the progress messages is a checkpoint yield, not a separate function call.
- **ErrorMessage is appended to `agent_loop_response` AND yielded (Bug 8)**: the `except Exception` handler doesn't just push the error to the frontend — it also appends the `ErrorMessage` to `agent_loop_response` before moving on to `state.finalize()` and the `PathExecutionResult` yield. That append is what lets downstream hooks (ChatModule detects it in `hook_after_event_execution` and stores the failed turn with `meta_data.status="failed"` instead of a normal user/assistant pair) see the failure signal. Without the append, hooks see a silently-truncated turn and happily persist it as "success with empty reply", which was exactly the Bug 8 contamination.

## Common New-Developer Mistakes

- Trying to add module data gathering here: all data gathering belongs in `ContextRuntime` (which calls `hook_data_gathering` on each module). This step only orchestrates.
- Assuming `ctx.execution_result` is set inside this generator: the router (`step_3_execute_path.py`) sets it after intercepting the `PathExecutionResult` yield.
- Forgetting that `skill_env_vars` must be a `dict[str, str]` — passing any other type will cause the SDK subprocess to reject it silently.
