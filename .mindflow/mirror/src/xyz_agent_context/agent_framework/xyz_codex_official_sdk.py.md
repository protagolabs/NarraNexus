---
code_file: src/xyz_agent_context/agent_framework/xyz_codex_official_sdk.py
stub: false
last_verified: 2026-06-08
---

## Why it exists

`CodexSDKv2` — the v2 wrapper around codex CLI, this time via the
OFFICIAL `openai-codex` Python SDK (JSON-RPC `app-server` mode)
rather than v1's hand-rolled `codex exec --json` subprocess
management.

Coexists with v1 (`xyz_codex_cli_sdk.CodexSDK`) through the
`agent_loop_driver` registry: v1 keeps the `codex_cli` /
`codex` registration as the default; v2 registers under
`codex_cli_v2` / `codex_official` as opt-in. Users switch by setting
`user_slots.agent_framework = "codex_cli_v2"` or
`AGENT_LOOP_FRAMEWORK=codex_official` env override. Default cutover
to v2 is a separate Phase 3 PR.

Implements the same async-generator contract as
`xyz_claude_agent_sdk.ClaudeAgentSDK` and
`xyz_codex_cli_sdk.CodexSDK`, conforming to
`agent_loop_driver.AgentLoopDriver` Protocol via structural typing.

## Design decisions

- **Wire protocol**: SDK uses codex CLI's JSON-RPC `app-server` mode
  (codex daemon process + RPC client), not v1's `exec --json` mode
  (one-shot stdout parsing). The richer protocol surfaces 30+
  notification types including `ReasoningTextDeltaNotification` /
  `ReasoningSummaryTextDeltaNotification` / `AgentMessageDeltaNotification`
  — these stream tokens incrementally, fixing v1's "Thinking panel
  appears all at once" UX bug.

- **MCP config via `CodexConfig.config_overrides`, not a file**:
  v1 writes `$CODEX_HOME/config.toml` and lets codex CLI read it on
  start. v2 passes the same TOML-literal key=value strings as a tuple
  through `CodexConfig.config_overrides` (sugar for `--config k=v`
  flags). Same logical content (mcp_servers / sandbox_mode /
  model_reasoning_summary / model / permissions), different surface
  — no filesystem write of the config block. Helper
  `_build_codex_config_overrides` builds the tuple; mirrors v1's
  `_codex_config_toml_builder.build_codex_config_toml`.

- **Cancellation via `TurnHandle.interrupt()`, not subprocess kill**:
  v1's race-with-cancel + SIGTERM 5s + SIGKILL becomes a single
  server-side RPC `interrupt`. < 1s release vs v1's 1–5s. Implemented
  as: before each yield in the streaming loop, check the
  NarraNexus cancellation token; if set, call `handle.interrupt`
  through `asyncio.to_thread` (the SDK's interrupt method is sync)
  and break out of the stream.

- **Sync iterator → async generator bridge**: `TurnHandle.stream()`
  returns a synchronous `Iterator[Notification]`. We wrap it in
  `_aiter_stream` which calls `next(stream, _SENTINEL)` inside
  `asyncio.to_thread` per item — keeps the FastAPI event loop
  unblocked so WS broadcaster, hook tasks, etc. interleave normally.
  StopIteration → `_SENTINEL` → generator returns; no exception
  round-trip per termination.

- **Sandbox stays at `danger-full-access`**: codex CLI issue #16685
  (MCP tool calls auto-cancel under `read-only` / `workspace-write`)
  is documented against `exec` mode. We don't have evidence yet about
  app-server mode's behavior, so v2 keeps the v1 workaround. NarraNexus's
  application-layer guards (per-agent `working_path` + `[permissions]`
  deny rules from the translator) remain the effective sandbox.

- **Reasoning summary = `detailed`**: same rationale as v1 — codex's
  `none` default leaves the Thinking panel empty; `detailed` surfaces
  the most useful natural-language summary OpenAI exposes. Now PLUS
  v2's streaming delta means the panel fills in character-by-character.

- **NarraNexus concerns NOT in SDK, kept in v2 unchanged**:
  - `_build_system_prompt_and_user_msg` (imported from v1's module) —
    NarraNexus assembles a 70k+ char system prompt with source-aware
    history eviction; SDK has `base_instructions` kwarg but no
    document support for that size, so we use the file route via
    `model_instructions_file` in `config_overrides`.
  - `_stage_codex_oauth_credentials` (imported from v1) — SDK reads
    auth from `$CODEX_HOME/auth.json`; we still need to copy the host
    auth.json into our per-run tempdir so the SDK subprocess inherits
    a valid credential without modifying the user's `~/.codex/`.
  - `_sse_url_to_streamable_http` (imported from v1) — MCP URL
    rewriter (SSE → streamable HTTP form codex CLI's MCP client
    requires). NarraNexus-specific.
  - `_codex_permission_translator.translate_tool_policy_to_codex_permissions`
    — CC tool policy → codex permissions dict. Unchanged.

- **Translation in `output_transfer.py`, not inline**: v2 emits the
  same internal event shape `ResponseProcessor` already consumes from
  v1. The Notification → event mapping lives in
  `output_transfer._codex_official_to_openai_agents` so the
  agent_loop method stays under ~150 lines and the translation table
  is unit-testable independently.

- **One-way reuse of v1 helpers, NOT a refactor**: v2 imports from
  `xyz_codex_cli_sdk` directly during the coexistence period. When
  v1 retires (Phase 3 cutover PR), the shared helpers move into
  `_codex_common.py` and both files import from there. Doing the
  refactor before retirement would inflate the v2 PR's diff
  unnecessarily.

## Upstream / downstream

- **Upstream**: `agent_runtime._agent_runtime_steps.step_3_agent_loop`
  via `agent_framework.get_agent_loop_driver(framework='codex_cli_v2', ...)`.
  Dispatched by `_resolve_agent_framework_name` reading
  `user_slots.agent_framework`.

- **Downstream**:
  - `openai_codex.AsyncCodex` / `Thread` / `TurnHandle` (official SDK).
  - `_build_codex_config_overrides` (local helper) — replaces v1's
    `_codex_config_toml_builder.build_codex_config_toml`.
  - `_aiter_stream` (local helper) — bridges SDK's sync iterator
    into our async loop.
  - `output_transfer.output_transfer(transfer_type="codex_official")`
    — Notification dict → internal event shape.
  - `_codex_permission_translator.translate_tool_policy_to_codex_permissions`
    — unchanged from v1.
  - `xyz_codex_cli_sdk._build_system_prompt_and_user_msg` /
    `_stage_codex_oauth_credentials` / `_sse_url_to_streamable_http`
    — imported directly (cross-file reuse).
  - `api_config.codex_config` — ContextVar for per-call config.

## Gotchas

- **`openai-codex` 0.1.0b3 is a pre-release**. `pyproject.toml`
  needs `[tool.uv] prerelease = "allow"` set or `uv lock` refuses
  to resolve. Tighten this back when the SDK reaches 1.0.

- **SDK ships codex CLI binary as a wheel** (`openai-codex-cli-bin`).
  That's a ~90 MB wheel dependency. Verified working on macOS arm64
  + Linux x86_64 manylinux. Untested on Windows; if Windows support
  matters, expect to either ship a separate codex install path or
  rely on the wheel's Windows variant if/when published.

- **`TurnHandle.stream()` is a SYNC iterator** despite the rest of
  the SDK being async-friendly via `AsyncCodex` / `AsyncThread`.
  `_aiter_stream` bridges it; never iterate `stream` directly in an
  async function, you'll block the event loop.

- **`Notification` is a dataclass with `method: str` and `payload:
  NotificationPayload`**, where `NotificationPayload` is a pydantic
  Union. When we `.model_dump()` the notification we get
  `{"method": "...", "payload": {...}}`, with the payload being the
  specific notification body. Translator dispatches on `method`.

- **`ThreadItem` is a pydantic `RootModel` Union** of 16 specific
  item types (AgentMessageThreadItem, ReasoningThreadItem,
  McpToolCallThreadItem, CommandExecutionThreadItem, etc.). When
  serialized to dict, the inner item appears at top level OR nested
  under `"root"` depending on pydantic dump options — translator
  unwraps `"root"` if present.

- **`Codex` and `AsyncCodex` share the same `CodexConfig` shape**;
  the only difference is the methods return sync vs. async objects.
  v2 uses `AsyncCodex` exclusively because NarraNexus's backend is
  async-first.

- **`thread.turn(input)` vs `thread.run(input)`**: the spike
  initially looked for `run_streamed`, but the SDK uses `turn()` to
  start a streaming turn (returns `TurnHandle`) and `run()` for a
  buffered call (returns `TurnResult`). v2 uses `turn()` + handle
  streaming. `run()` would be a cleaner API for non-streaming
  contexts (tests?) but we go straight to streaming since the agent
  runtime always wants progressive output.

- **The agent_loop method assumes `inspect.iscoroutine` on
  `thread_start` and `turn` calls**: defensive runtime check because
  SDK 0.1.0bN may flip methods between sync and async between
  patch releases. Both branches are exercised in production paths;
  if a future version of the SDK consistently makes them coroutines,
  the conditional becomes dead code but doesn't hurt.

## What v1 did that v2 does NOT (yet) handle

- **`[CodexSDK][raw]` raw event log diagnostic**: v1 dumps the
  literal JSON Lines bytes at INFO level for debugging. v2 emits
  pydantic models — the equivalent is `notification.model_dump()`
  but we don't log it by default (DEBUG only). Add an INFO-level
  raw dump back if a similar debug session needs it.

- **stderr capture into a list + WARNING per line**: v1 reads codex
  CLI stderr directly. SDK swallows stderr into its internal client.
  Errors that codex CLI emits to stderr (config warnings, OAuth
  prompts, deprecation notices) now arrive as
  `ConfigWarningNotification` / `DeprecationNoticeNotification` —
  the translator currently drops these to DEBUG. Surface as
  WARNING if ops needs visibility.

- **5-second SIGTERM grace before SIGKILL**: v1's belt-and-braces.
  v2 trusts `handle.interrupt()` to release cleanly. If a turn
  gets stuck in a way `interrupt` can't release (network freeze
  mid-RPC?), we'd hang — observable as the same "Stop button does
  nothing" symptom v1 had under similar conditions, but rarer.

## New-person traps

- **Don't add another `uv sync` to dev-local.sh.** That stripped the
  editable install of `xyz-agent-context` and broke startup for
  hours during the v1 stabilization. v1's `bash run.sh` heal path
  already covers the case; v2 inherits that and doesn't need its
  own.

- **`pyproject.toml` `[tool.uv] prerelease = "allow"`** must stay
  while `openai-codex` is on a beta version. Removing it makes
  `uv lock` fail. The right time to remove is when both
  `openai-codex` AND `openai-codex-cli-bin` reach stable
  (no `aN` / `bN` suffix).

- **The translator drops `command_execution_output_delta` and
  `mcp_tool_call_progress` deltas** intentionally. Promoting them
  to real events requires frontend support for streaming tool output
  which we don't have yet. Adding them blind without UI changes
  means broken-looking events landing in the runtime.

- **Sandbox name has TWO conventions, kept on purpose**:
  * codex internal config / TOML / CLI: `danger-full-access` (used in
    `config_overrides` as `sandbox_mode="danger-full-access"`)
  * `openai_codex` Python SDK enum: `Sandbox.full_access`
    (the SDK dropped the "danger" prefix — same mode underneath)

  Initial v2 commit incorrectly used `Sandbox.danger_full_access` at
  the `thread_start` kwarg, crashed immediately at first real turn on
  2026-06-08 (`AttributeError: type object 'Sandbox' has no attribute
  'danger_full_access'`). Test `test_sandbox_full_access_attribute_exists`
  now locks in the SDK contract — if 0.2 renames the enum again,
  fail loud at test time, not at user time.

  **Both layers must stay set** — config_overrides is the persisted
  TOML; the kwarg is the per-thread override. Never trim either one
  "for simplicity" thinking the other covers it.
