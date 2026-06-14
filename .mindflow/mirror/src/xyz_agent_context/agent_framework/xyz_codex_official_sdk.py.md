---
code_file: src/xyz_agent_context/agent_framework/xyz_codex_official_sdk.py
stub: false
last_verified: 2026-06-12
---

## 2026-06-12 — sandbox_mode 改为 env 可配（测 #16685 / 上线 workspace 隔离）

PR #25 review §1/§2:云端 codex 现在 `danger-full-access` = OS 沙箱关着,
无文件系统隔离(能读别租户 workspace + 共享 `~/.codex/auth.json`)。目标是切
`workspace-write` + `writable_roots=[working_path]` 拿内核级隔离;唯一障碍
#16685(非 full 沙箱下 MCP 调用被自动取消),而本文件注释说 v2 app-server
模式可能没这 bug —— 需实测。

把 sandbox 模式做成 **`CODEX_SANDBOX_MODE`** 环境变量:默认 `danger-full-access`
(行为不变),可选 `workspace-write` / `read-only`。`_resolve_sandbox_mode()`
解析(非法值回默认 + warn);两处都用它——`_build_codex_config_overrides`
(写 `sandbox_mode="…"` + 已有 `writable_roots`)和 `thread_start(sandbox=)`
(经 `_SANDBOX_ENUM_ATTR` 映射到 SDK `Sandbox` 枚举 full_access/workspace_write/
read_only;枚举名不对时打印可用成员并回退 full_access,不静默吞)。加了
`[CodexSDKv2] sandbox_mode=…` INFO 日志。

测法:Mac 上 `CODEX_SANDBOX_MODE=workspace-write` 重启 backend,跑带 MCP 的
codex 对话,看 MCP 工具是否被取消。不取消 → 可把默认改 workspace-write 拿真隔离。

**实测结果(2026-06-12,run_b35e0272):** `sandbox_mode=workspace-write` 下,
MCP 工具调用经 v2 的 `item/autoApprovalReview`(`decision_source=agent`、
low-risk、approved)**自动批准并正常完成**(`item/completed status=completed`)。
→ **#16685 在 v2 app-server 模式不成立**(它是旧 exec 模式没人应答审批的 bug;
v2 有 auto-reviewer)。

**因此默认改为按部署模式分流**(`_resolve_sandbox_mode` 接 `get_deployment_mode()`):
**cloud → `workspace-write`**(多租户内核级隔离,review §1/§2 落地)、
**local → `danger-full-access`**(自己的机器,不变;和 `_tool_policy_guard`
只在 cloud 收紧一致)。`CODEX_SANDBOX_MODE` 仍可强制覆盖任一。

## 2026-06-11 — API-key 鉴权回归：补回 model_provider + env_key

v2 切到 `config_overrides` 后，`_build_codex_config_overrides` 漏掉了 v1
`_codex_config_toml_builder` 里的 `[model_providers.narranexus]` 块。
后果分叉：

- **OAuth**：codex 读 stage 进 `$CODEX_HOME/auth.json` 的凭证 → 正常。
- **API key**：`CodexConfig.to_cli_env` 设了 `CODEX_API_KEY`，但**没人
  告诉 codex 去读它**——codex 内置 openai provider 默认走 OAuth，于是裸
  调 `api.openai.com/v1/responses`，无认证头 → `401 Missing bearer`，
  每轮失败（incident 2026-06-11，前端表现为 "Reconnecting... 1/5~5/5"
  后报错）。

修复：`_build_codex_config_overrides` 新增 `api_key` / `base_url` /
`auth_type` 参数；当 `auth_type=="api_key"` 且有 key 时，补回 v1 验证过
的那组 override：`model_provider="narranexus"` +
`model_providers.narranexus.{base_url, env_key="CODEX_API_KEY",
wire_api="responses"}`（base_url 空则默认官方 OpenAI）。**key 本身绝不
写进 config_overrides**，只通过 `env_key` 指向的 `CODEX_API_KEY` env 传。
OAuth 路径不动。测试见 test_codex_sdk_v2_init.py 的
`test_overrides_declares_model_provider_for_api_key` 等。

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

- **Cutover 2026-06-08 — single canonical name, no aliases, v1 file dormant**:
  Only ``codex_cli`` is registered, mapping to ``CodexSDKv2``. The A/B
  period briefly registered four aliases (``codex`` / ``codex_cli`` /
  ``codex_cli_v2`` / ``codex_official``) but those were
  backwards-compat shims — per binding rule #2 (YOLO), the cleanup
  dropped everything except the canonical id. ``DEFAULT_AGENT_LOOP_FRAMEWORK``
  also moved from legacy ``"claude"`` shorthand to ``"claude_code"``
  in the same pass.

  Hand-rolled v1 ``CodexSDK`` in ``xyz_codex_cli_sdk.py`` is still
  importable (the file is kept intentionally) but NOT registered —
  pulling it back online if v2 has a critical regression requires
  one ``register_agent_loop_driver("codex_cli", CodexSDK)`` line in
  ``agent_framework/__init__.py``.

  Frontend dropdown only shows ``Codex CLI`` (one option). Existing DB
  rows with ``agent_framework="codex_cli"`` transparently run v2 on
  the next turn. Existing DB rows with the dropped A/B aliases
  (``codex_cli_v2`` / ``codex_official``) fail loud with
  ``ValueError`` — the user re-picks "Codex CLI" from Settings to fix
  (chosen over silent migration to align with binding rule #2).

  Diagnostic logs (``method tally`` / ``item-type tally``) added
  during the v2 stabilisation period also removed in this cleanup —
  if v2 ever needs debugging again, re-add the same blocks; they're
  small and self-contained.

- **Translator method names follow `item/*` namespace, NOT `turn/*`**:
  The single biggest bug shipped in the initial v2 commit. SDK
  notifications live in two namespaces:
  * `thread/*`, `turn/*` — thread + turn lifecycle (started, completed, etc.)
  * `item/*` — everything per-item (started, completed, deltas, progress)

  Initial commit had EVERY item notification name on `turn/*`
  (`turn/itemStarted`, `turn/agentMessageDelta`,
  `turn/reasoningSummaryTextDelta`, etc.). Real names use
  forward-slash sub-paths: `item/started`, `item/agentMessage/delta`,
  `item/reasoning/summaryTextDelta`. Result: translator silently
  dropped 12 out of 14 notification types. Reasoning leaked into
  the main chat bubble; tool calls leaked as text. Fallback LLM
  caught the request and produced visible output (so it didn't
  *look* broken — just looked like v2 was a worse model).

  Canonical source: `openai_codex.generated.notification_registry.NOTIFICATION_MODELS`
  (a `dict[str, type]` shipped with the SDK). Contract test
  `test_method_constants_match_sdk_notification_registry` imports
  this registry and asserts every `_METHOD_*` constant in
  `output_transfer.py` is a real key. Future SDK rename = CI red.

- **There is NO `turn/failed` notification**: failed turns surface
  via `turn/completed` with `turn.status == "failed"` and
  `turn.error` populated. Initial draft listened for `turn/failed`
  which would have silently looked like a success.

- **`AsyncTurnHandle.stream()` is an async generator, NOT a sync iterator**:
  Initial draft wrapped `next(stream)` in `asyncio.to_thread` —
  built on the wrong assumption that it was sync. Real API:
  `async for notification in handle.stream(): ...`. Contract test
  `test_stream_is_async_generator_function` locks this in.

- **`AsyncTurnHandle.interrupt()` is a coroutine, NOT a sync method**:
  Same issue — initial draft used `asyncio.to_thread(handle.interrupt)`.
  Real API: `await handle.interrupt()`. Contract test
  `test_interrupt_is_coroutine_function` locks this in.

- **`AsyncThread.turn` is a coroutine**: `await thread.turn(input)`
  directly, no defensive `inspect.iscoroutine` ladder. Locked in by
  `test_turn_is_coroutine_function`.

- **`thread_start` kwargs are SDK-specific, NOT v1 CLI flags**:
  v1 used `codex exec --skip-git-repo-check` to suppress codex's
  "cwd must be a git repo" guard. I assumed `AsyncCodex.thread_start`
  exposed the same kwarg; it doesn't. SDK 0.1.0b3 signature only
  accepts: `approval_mode, base_instructions, config, cwd,
  developer_instructions, ephemeral, model, model_provider,
  personality, sandbox, service_name, service_tier,
  session_start_source, thread_source`. Passing an unknown kwarg
  is `TypeError` at first turn (incident 2026-06-08 v3 — same day,
  different bug than the Sandbox rename).

  The git-repo guard in app-server mode is bypassed by
  `sandbox_mode="danger-full-access"` in `config_overrides`. If a
  future SDK reintroduces an explicit check, route the equivalent
  flag through `CodexConfig.launch_args_override=("--skip-git-repo-check",)`
  instead of inventing a non-existent kwarg.

  Test `test_thread_start_accepts_kwargs_we_actually_pass`
  inspects the live signature and asserts every kwarg we pass is
  present — SDK upgrade that drops one fails CI before users.

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
