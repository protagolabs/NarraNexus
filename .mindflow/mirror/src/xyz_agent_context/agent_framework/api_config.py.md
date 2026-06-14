---
code_file: src/xyz_agent_context/agent_framework/api_config.py
last_verified: 2026-06-10
stub: false
---
## 2026-06-10 — one-key onboarding: AnthropicHelperConfig joins the config stack

New `AnthropicHelperConfig` (api_key/base_url/model/auth_type) carries the
helper_llm config when that slot points at an anthropic-protocol provider —
the single-Claude-key path. It rides a new `_anthropic_helper_ctx` ContextVar
+ `anthropic_helper_config` proxy (holder keeps a benign empty default).
`set_user_config(claude, openai, codex=None, anthropic_helper=None)` — a call
WITHOUT the new arg resets the ctx to None, which is what makes
`get_helper_sdk()` dispatch safe across tasks. `RuntimeLLMConfigs` gains
`anthropic_helper: Optional[...] = None`. The legacy strict fallback's helper
block now branches on the provider protocol (anthropic → AnthropicHelperConfig,
`.openai` left empty). `setup_mcp_llm_context` upgraded from the 2-tuple path
to `get_agent_owner_runtime_llm_configs` so MCP tool processes see codex +
anthropic_helper too. `CodexConfig` gains neutral `thinking`/`reasoning_effort`
(mirror of ClaudeConfig's; dialect mapping in _codex_config_toml_builder).

## 2026-06-10 — merge `dev` into codex branch: embeddings out, Codex stays

Reconciling two opposite directions: `dev` retired embeddings (narrative/
memory routing is BM25 now), while the codex branch had made an embedding
slot *required*. Resolution = follow `dev`. `EmbeddingConfig`,
`_embedding_ctx`, the embedding field on `RuntimeLLMConfigs`, and the
embedding slot in the strict resolver are all gone. `RuntimeLLMConfigs` is
now `{claude, openai, codex}`; `set_user_config(claude, openai, codex=None)`;
`get_user_llm_configs` is back to a 2-tuple `(claude, openai)` — Codex rides
the `*_runtime_*` accessors. The guardrail test `test_embedding_removal.py`
was updated so the `set_user_config` signature assertion expects
`(claude, openai, codex)` (still rejects any embedding arg).

## 2026-06-10 — ClaudeConfig carries neutral reasoning params

`ClaudeConfig` gained `thinking` / `reasoning_effort` (both default ""
= auto), populated from the agent slot's SlotConfig at all three
construction sites (llm_config.json path, .env fallback — stays auto —
and the per-user resolver). The fields are framework-neutral; the
Claude-dialect mapping lives in xyz_claude_agent_sdk
(`_resolve_reasoning_options`), NOT here. `to_cli_env()` is untouched —
these ride ClaudeAgentOptions, not env vars.


## 2026-05-29 — add CodexConfig + codex_config ContextVar

Symmetric with the existing ClaudeConfig/OpenAIConfig/EmbeddingConfig
trio. New ``CodexConfig`` frozen dataclass carries ``api_key`` /
``base_url`` / ``model`` / ``auth_type`` for the Codex CLI subprocess
spawned by ``xyz_codex_cli_sdk.CodexSDK``. ``to_cli_env()`` mirrors
the ClaudeConfig invariant: explicit blank for ``CODEX_API_KEY``
when not in use so a parent-process env can't leak across tenants.

``base_url`` / ``model`` are NOT exported via env — Codex reads them
from per-run ``config.toml`` ``[model_providers.<name>]`` instead.
The wire is via ``_codex_config_toml_builder``.

Per-task ContextVar (``_codex_ctx``) + ``_ConfigHolder._codex`` slot
follow the existing pattern. Holder is initialised to an empty
``CodexConfig()`` by default — there is no .env/llm_config.json
source path because Codex auth flows through ``codex login`` (host
CLI) rather than NarraNexus config. Per-user overrides arrive via
the ContextVar at agent_loop time.

## 2026-05-31 — runtime config bundle includes CodexConfig

`RuntimeLLMConfigs` groups the four per-turn configs: Claude agent,
helper LLM, embedding, and Codex agent. `get_user_runtime_llm_configs()`
and `get_agent_owner_runtime_llm_configs()` return this bundle so
`AgentRuntime.run()` can inject `codex_config` before Step 3 selects
`CodexSDK`. The older `get_user_llm_configs()` still returns the three
non-Codex configs for call sites that do not drive the agent loop.

`CodexConfig` now carries `auth_ref` in addition to api key / base URL /
model. It is not exported as an env var; `xyz_codex_cli_sdk` uses it to
copy the host `codex login` auth file into the per-run `CODEX_HOME`.

## 2026-05-22 — to_cli_env injects API_TIMEOUT_MS + CLAUDE_CODE_MAX_RETRIES (#7)

`ClaudeConfig.to_cli_env()` now also sets `API_TIMEOUT_MS` (from
`settings.llm_api_timeout_ms`) and `CLAUDE_CODE_MAX_RETRIES` (from
`settings.llm_max_retries`). These are the Claude Code CLI's own knobs for a
per-REQUEST timeout and built-in transient-error retry. Previously unset →
inherited CLI defaults; now explicit + .env-tunable so a stalled request is
bounded and auto-retried (the "卡死无重试" fix). API_TIMEOUT_MS is per-request,
NOT a run total — it does not violate 铁律 #14 (no agent_loop cap); retry is on
the SAME provider so it does not govern model choice (铁律 #15).

## 2026-05-13 — `_get_user_llm_configs_strict` delegates to provider_driver

The user-provider branch now first calls
`provider_driver.resolve_user_llm_configs(user_id, db)`. That function
encapsulates the new single-point resolution path including reverse-
validation self-heal for broken slot.model bindings (the Xiong bug).
If the new resolver raises `LLMConfigNotConfigured` we re-raise to keep
the actionable message; any other exception logs a warning and falls
through to the legacy hand-rolled branch below — kept as a safety net
during the Phase 1 confidence window.

The legacy `_use_system_default_strict` path is untouched. The cloud
migration that turns env-var system credentials into a regular
`user_providers` row with `owner_user_id=NULL` (Phase 3) will collapse
that branch too; until then, opt-in `prefer_system_override=true` users
keep going through the old path.

See `reference/self_notebook/specs/2026-05-13-provider-unification-design.md`.

## 2026-04-20 change — strict 2-branch `get_user_llm_configs` (Bug 2)

The old 4-branch tree silently fell back to the system free tier whenever
`_get_user_llm_configs_strict` raised. That masked real configuration
errors and also depended on `QuotaService.default()` being bootstrapped
at process start — which `run_lark_trigger` had forgotten to do,
rendering the fallback permanently unreachable from the Lark process
(root cause of Bug 2 silent no-reply on Lark).

The new tree is driven solely by `user_quotas.prefer_system_override`:

  - `True`  → strict system free tier; raise `SystemDefaultUnavailable`
              (disabled by admin / quota exhausted). No silent fallback
              to the user's own provider.
  - `False` → strict user's own provider; raise
              `LLMConfigNotConfigured`. No silent fallback to the system
              free tier.

Error classes form a hierarchy:
  `RuntimeError` ← `LLMResolverError` ←
      `LLMConfigNotConfigured` / `SystemDefaultUnavailable`.

Consumers that want "any resolver failure" catch `LLMResolverError`;
consumers that want to branch UX per type catch the concrete subclass.
`AgentRuntime.run` catches the base class and yields a structured
`ErrorMessage(error_type=<subclass name>)`.

The new helper `_ensure_quota_service()` lazy-bootstraps
`QuotaService.default()` on first use via the shared `get_db_client()`.
Every entry point (backend.main, job_trigger, bus_trigger,
run_lark_trigger, standalone MCP runner) now works out-of-the-box
without each calling `bootstrap_quota_subsystem` itself — the trigger
that forgot is no longer a ticking bomb.

## 2026-04-16 addition — provider_source + current_user_id ContextVars

Two new auxiliary ContextVars were added alongside the existing
claude/openai/embedding ones, supporting the system-default free-tier
quota feature:

- `provider_source` ("user" | "system" | None) — set by ProviderResolver
  to signal which config branch produced the active user_config, so
  cost_tracker can decide whether to deduct the system quota after an
  LLM call.
- `current_user_id` — set by auth_middleware once the JWT is parsed, so
  cost_tracker can attribute usage without threading `user_id` through
  every layer of the LLM call stack.

Both default to None. Local mode / tests / any path that does not hit
auth_middleware simply sees None, making the quota hook a silent no-op.
Claim: these additions do NOT alter existing behaviour of `set_user_config`,
`_ConfigProxy`, or any proxy object — they are strictly additive.

# api_config.py — Centralized LLM config with per-task isolation

## 为什么存在

整个 agent_framework 层有四个不同的 LLM 消费方（ClaudeAgentSDK、OpenAIAgentsSDK、GeminiAPISDK、EmbeddingClient），每个都需要 API key、base_url 和 model name。如果各自读 `settings` 或 `os.environ`，在多租户并发场景下不同用户的 agent turn 会互相污染 API key（Alice 的 agent 用了 Bob 的 key）。这个文件提供一个统一的入口，用两级机制解决：全局 `_ConfigHolder`（延迟加载、可热重载）+ per-task `ContextVar`（asyncio task 级别隔离）。

## 上下游关系

所有使用 LLM 的组件都从这里读配置，而不直接读 `settings`：`xyz_claude_agent_sdk.py` 读 `claude_config`，`openai_agents_sdk.py` 读 `openai_config`，`embedding.py` 读 `embedding_config`，`gemini_api_sdk.py` 读 `gemini_config`。

上游写入者：`agent_runtime.py` 在每次 `run()` 入口调用 `get_agent_owner_llm_configs()` 然后 `set_user_config()`，把 owner 的三个 slot 配置注入当前 asyncio task 的 ContextVar。背后由 `user_provider_service.py` 从数据库的 `user_providers`/`user_slots` 表读取。本地单机模式的全局配置则来自 `provider_registry.py` 读取 `~/.nexusagent/llm_config.json`，fallback 到 `settings.py`。

## 设计决策

**ContextVar 而非全局变量**：`asyncio.Task` 创建时复制父 context，`asyncio.gather()` 内的每个 task 天然隔离。如果用全局 `_holder` 的 mutation，并发 trigger（`bus_trigger`、`job_trigger`）处理不同 owner 的 agent 时会 race condition。ContextVar 无需加锁，且在 task 结束后自动失效。

**`_ConfigProxy` 的类型欺骗**：`claude_config` 变量被标注为 `ClaudeConfig` 但实际是 `_ConfigProxy`。这是有意识的权衡——调用方代码写 `claude_config.model` 和以前完全一样，不需要改，但类型检查器会漏掉错误。代码内已有详细 TODO 说明正确解法（显式 `RuntimeContext` 参数传递，改动约 20 个文件）。

**LLM billing 归属于 agent owner 而非触发者**：`get_agent_owner_llm_configs()` 总是查 `agents.created_by` 作为计费主体，不用调用方传入的 `user_id`（后者可能是 Matrix sender、job target 等非 owner 身份）。

**Gemini 不走 ContextVar**：Gemini 仍从 `settings.py` 加载，尚未纳入三 slot 体系（代码注释有标注 "not part of the slot system yet"）。

## Gotcha / 边界情况

- `dimensions` 字段故意不传给 API：传了会在切换 embedding model 时造成 `SchemaNotReadyException`（不同模型原生维度不同，带 dimensions 参数调 API 会 400）。这个决策在注释里有解释，但容易被后续开发者"修复"回去。
- `auth_type="oauth"` 的 `ClaudeConfig` 的 `api_key` 是空字符串，`_holder.reload()` 里有 `json_claude if (json_claude.api_key or json_claude.auth_type == "oauth")` 的特判，新增判断逻辑时要同样处理 oauth 情况。
- `reload_llm_config()` 只重置全局 `_holder`，不影响已运行 task 的 ContextVar 值——hot-reload 对当前正在执行的 agent turn 无效，只对下一次 turn 生效。

## 新人易踩的坑

- 在没有调用 `set_user_config()` 的代码路径（如单元测试、独立脚本）里读 `claude_config.model` 会穿透 ContextVar 到全局 `_holder`，行为取决于环境配置。测试时最好 patch `api_config` 模块级别的代理对象或 patch `_holder`。
- 不要把 `embedding_config.dimensions` 传给 OpenAI embeddings API 调用，虽然 `EmbeddingConfig` 有这个字段但它只用于 UI 展示，真正的请求故意不带它。
- `LLMConfigNotConfigured` 是 `RuntimeError` 子类，在 `agent_runtime.py` 的 run() 里被捕获后会 yield `ErrorMessage` 给前端并 return，不会继续执行后续步骤。
