---
code_file: src/xyz_agent_context/agent_framework/cli_helper_sdk.py
last_verified: 2026-07-07
stub: false
---
# cli_helper_sdk.py — CLI-backed helper LLM（订阅同时覆盖 Helper）

## 为什么存在

当 helper_llm 槽位指向订阅（OAuth）provider —— Claude Code（`claude_oauth`）或
Codex（`codex_oauth`）—— 那份 OAuth 凭据**无法直连 Messages/Chat-Completions API**，
所以 helper 的小结构化调用改为**走同一个 CLI 一次性执行**。这就是"一次订阅登录同时覆盖
agent 主模型和 Helper LLM、无需二次配置"的实现（2026-07 P0）。

与 `OpenAIAgentsSDK` / `AnthropicHelperSDK` 接口一致（`llm_function` / `llm_stream`），
~15 个 helper 调用点通过 `get_helper_sdk()` 无感使用，绝不直接 import 本类。

两种后端，按 `cli_helper_config.framework` 选：
- **claude_code** → `claude_agent_sdk.query()` 一次性（`max_turns=1`、`allowed_tools=[]`、
  无 MCP、`cwd` 用中性临时目录），复用 agent loop 同款 `ClaudeConfig.to_cli_env` 凭据链
  （OAuth 时 key 留空，CLI 读 `~/.claude` 凭据）。`ResultMessage.usage` 给 token。
- **codex_cli** → 复用已注册的 codex agent-loop driver 一次性（复用其 CODEX_HOME/凭据
  staging 与解析），累积 `response.text.delta`，从终态事件读 usage。**best-effort**：codex
  是编码 agent 而非补全 API，结构化 JSON 靠 schema-in-prompt + 提取兜底。

结构化输出用与 AnthropicHelperSDK 相同的 prompt-engineered 路径（schema 塞进 system
prompt，客户端提取+校验 JSON），复用其 `_extract_json_from_llm_output` /
`_ParsedResult` / `_SimpleResult` / `record_cost`，下游看到的形状完全一致。

## 上下游

- 上游：`get_helper_sdk()`（helper_sdk.py）在 `_cli_helper_ctx` 被设置时 dispatch 到这里
  （优先级 cli > anthropic > openai）。
- `_cli_helper_ctx` 由 resolver 在 helper_llm 槽位是 OAuth provider 时装配
  （`build_cli_helper_config` → `RuntimeLLMConfigs.cli_helper` → `set_user_config`）。

## 陷阱

- OAuth 订阅调用可能上报 0 token（CLI 计费在订阅侧，不在我们）——有则记账，无则 warn 不报错。
- 成本上下文来自 `get_cost_context()`（agent_id, db），与其它 helper 一致。
