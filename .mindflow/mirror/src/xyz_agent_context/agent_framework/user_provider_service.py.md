---
code_file: src/xyz_agent_context/agent_framework/user_provider_service.py
last_verified: 2026-06-02
stub: false
---
# user_provider_service.py — 多租户场景的 per-user provider 数据库服务

## 为什么存在

云端部署时，每个用户有自己的 API key 和模型偏好，不能共用单一的 `llm_config.json` 文件。这个服务把 provider 配置从文件系统迁移到数据库的 `user_providers` 和 `user_slots` 表，实现 per-user 隔离。接口设计刻意对齐 `provider_registry.py`，让调用方代码可以相对平滑地切换。

## 上下游关系

被 `api_config.py` 的 `get_user_llm_configs()` 和 `get_agent_owner_llm_configs()` 调用，在每次 agent turn 开始时加载 owner 的 LLM 配置。被 `backend/routes/` 中的 provider 管理 API 路由调用处理用户的 Settings 操作。

在做连接测试时，委托给 `provider_registry.provider_registry.test_provider()`，复用已有的测试逻辑，不重复实现。

`_is_cloud_mode()` 检查 `DATABASE_URL` 是否以 `sqlite` 开头来判断运行模式，但这个函数目前只是辅助判断，不决定哪些代码路径被使用——数据库存储始终被使用，区别在于是否回退到 `llm_config.json`（那个逻辑在 `api_config.py` 的 `_ConfigHolder` 里）。

## 设计决策

**和 `provider_registry.py` 的接口对称**：都有 `add_provider`、`remove_provider`、`set_slot`、`validate_slots`、`test_provider`。这让上层代码可以以相同方式操作两种存储后端，虽然目前没有统一抽象基类（将来可以提取）。

**Agent slot 协议由 `agent_framework` 决定**：`set_slot()` 不能只看静态 `SLOT_REQUIRED_PROTOCOLS`。当 `user_slots[agent].agent_framework == "codex_cli"` 时，agent slot 接受 OpenAI-protocol provider；默认/Claude Code 路径仍要求 Anthropic。Codex OAuth provider 创建时也直接写入 `driver_type="codex_oauth"` 和 `auth_ref="codex-cli:~/.codex/auth.json"`，避免等待启动 backfill 才能被 resolver 使用。

**Codex provider 的 `models` 列表 = codex CLI 自己的 curated picker**：seed 是 `["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"]`——2026-06-02 直接看 codex CLI 交互式 "Select Model and Effort" 菜单确认。`gpt-5.5` 是 codex 自己标记 default flagship；`gpt-5.4` 是 strong everyday；`gpt-5.4-mini` 是 fast/cheap。Legacy 变种（`gpt-5-codex`、`gpt-5.2-codex`、`gpt-5.3-codex`）codex CLI 支持但不放 picker，要走 `codex -m <name>` 显式调用——不进 NarraNexus dropdown 默认列表。

**踩过的坑（写在这里防再犯）**：早期我们假设过 `gpt-5.4-codex` 存在（线性外推 "有 5.4-mini 就有 5.4-codex"）。**不存在**——OpenAI 5.4 系列只有 base/mini/nano，codex 路线 5.3 → 5.5 跳过了 5.4。之前 `codex exec --model gpt-5.4-codex` 返回 `"not supported when using Codex with a ChatGPT account"` 不代表模型存在，那是 OAuth gateway 对任意 codex 请求的统一拒绝字符串。

**API-key Codex 不需要专属 card_type**：早期我加过一个 `codex_api_key` card 类型，但功能上与"创建 Custom OpenAI provider + 把 slot 的 `agent_framework` 切到 `codex_cli`"完全等价——resolver 看 protocol=openai 就走 `_codex_config_from_card`，跟 source name 无关。OAuth 卡片有独立功能差异（auth.json 检测 + 凭据路径管理），API key 卡片没有。所以 API-key 路径走 `card_type="openai"` 即可，前端 "+ Custom OpenAI" 按钮就是入口。OAuth provider 仍保留独立 card_type 因为它有真实的 auth_ref 状态管理。

**models 字段以 JSON 字符串存储**：数据库里 `user_providers.models` 是 JSON 字符串（而非数组类型列），读取时用 `json.loads`，写入时用 `json.dumps`。这是为了保持对 SQLite 和 MySQL 的兼容性，避免数据库方言差异。

**linked_group 机制与 `provider_registry.py` 对应**：删除 provider 时先查 `linked_group`，找到同组所有 provider 一起删除，同时清掉对应的 slots。

**`_DUAL_PROVIDER_CONFIGS` 字典**：把 NetMind/Yunwu/OpenRouter 的双协议配置集中在一个字典里，比 `provider_registry.py` 的三个独立 builder 函数更紧凑，但内容是独立硬编码的，两处不共享。

## Gotcha / 边界情况

- 并发写同一用户的 provider 时存在 last-write-wins 竞态（upsert 操作），但云端场景每个用户通常只有一个活跃会话，风险低。
- `validate_slots()` 只检查三个 slot 是否存在，不校验 provider 的 API key 是否有效或 protocol 是否匹配 slot 要求（protocol 校验只在 `set_slot()` 里做）。

## 新人易踩的坑

- `user_providers.models` 和 `user_slots` 的 `updated_at` 用 ISO 8601 字符串存储（`datetime.now(timezone.utc).isoformat()`），而不是 datetime 对象。读回来需要 `datetime.fromisoformat()`。
- `get_user_config()` 不抛出异常，如果用户没有配置任何 provider，返回空的 `LLMConfig`，后续 `get_user_llm_configs()` 里才会因 slot 缺失抛出 `LLMConfigNotConfigured`。
