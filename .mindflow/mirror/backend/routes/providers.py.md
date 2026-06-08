---
code_file: backend/routes/providers.py
last_verified: 2026-06-08
stub: false
---

## 2026-06-08 — Route 层 `_SUPPORTED_AGENT_FRAMEWORKS` 不再独立维护

之前 `backend/routes/providers.py` 在第 358 行硬编码了 `("claude_code", "codex_cli")` 一份白名单，而 service 层 `UserProviderService._SUPPORTED_AGENT_FRAMEWORKS` 已经扩到了 4 个名字（codex_cli_v2 / codex_official 是 v2 别名）。结果前端 dropdown 选 v2 → route 层 400 "Unknown framework"，而 service 层根本接收得了。两份白名单永远会漂。

修法：route 层直接 import service 层的常量当 single source of truth：

```python
from xyz_agent_context.agent_framework.user_provider_service import (
    UserProviderService as _UserProviderServiceForFrameworks,
)
_SUPPORTED_AGENT_FRAMEWORKS = _UserProviderServiceForFrameworks._SUPPORTED_AGENT_FRAMEWORKS
```

附带的两处也同步：
- `_probe_agent_framework_auth(framework)`: codex 分支匹配 `("codex_cli", "codex_cli_v2", "codex_official")` —— v1/v2 共用 `~/.codex/auth.json`，probe 走同一 `CodexOAuthDriver`
- `set_agent_framework` 的 `_ensure_codex_installed()` 触发条件: 同上 —— v2 SDK 内部还是 spawn `codex` 二进制（app-server 模式），install 副作用对 v2 同样必要

**铁律：framework 名字白名单四处必须同步**：
1. `agent_framework/__init__.py` register_agent_loop_driver
2. `provider_driver/resolver._KNOWN_AGENT_FRAMEWORKS` + `_CODEX_FRAMEWORK_VALUES`
3. `user_provider_service._SUPPORTED_AGENT_FRAMEWORKS` （= 后端 single source of truth）
4. `frontend/src/components/settings/ProviderSettings.tsx` 的 `AGENT_FRAMEWORKS` + `CODEX_FRAMEWORK_IDS`

route 层 #4 后已经不算独立条目了，因为 import 自动跟 service。但 frontend 不能 import Python，仍需手 sync。

## 2026-05-18 — 关掉 query 参数 user_id 这条 identity channel

`_get_user_id` 以前同时认两个 user_id 源：`request.state.user_id`（middleware 设的）和 query 参数 `user_id`。这俩并存就是 IDOR 漏洞：客户端可以一边发 `X-User-Id: bob`、一边 `?user_id=alice`，让 backend 在不同分支看到不同身份。本次彻底关掉 query 通道——`_get_user_id` 只读 `request.state.user_id`，缺失就 401。所有 endpoint 也删掉了 `user_id: Optional[str] = Query(None)` 参数。

身份只能来自一个 channel：cloud=JWT、local=X-User-Id header。前端 ApiClient (`api.ts:getAuthHeaders`) 和 SettingsProviders 的 `authFetch` 现在都会同时发这两个 header（取决于 mode）。

例外：`/embeddings/status` 和 `/embeddings/rebuild` 仍然有 `user_id: str = Query(...)`，但它的语义是 **target user**（管理员视角的"我要查谁的"），不是 identity。后续可以加 staff 角色 check。

# routes/providers.py — LLM 提供商与 Slot 配置路由

## 为什么存在

系统支持多个 LLM 提供商（Anthropic、OpenAI 及兼容 API）和多个使用"槽位"（Slot）：主推理、嵌入向量、工具调用等。每个用户有自己独立的提供商配置，存储在 `user_providers` 和 `user_slots` 表里。这个路由提供提供商的增删查和 Slot 指配操作，以及两个特殊功能：Claude Code CLI 登录状态检查和嵌入向量迁移。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(providers_router, prefix="/api/providers")`；前端设置面板；`backend/auth.py` 的 `AUTH_EXEMPT_PATHS` 包含 `/api/providers/claude-status`
- **依赖谁**：
  - `UserProviderService`（来自 `xyz_agent_context.agent_framework.user_provider_service`）— 所有提供商和 Slot 操作
  - `xyz_agent_context.agent_framework.model_catalog` — 获取已知模型列表和建议值
  - `xyz_agent_context.schema.provider_schema` — `LLMConfig`、`SlotName`、`SLOT_REQUIRED_PROTOCOLS`
  - `xyz_agent_context.agent_framework.api_config` — 热重载配置（本地进程内）
  - `EmbeddingMigrationService` — 嵌入向量重建

## 设计决策

**`_get_user_id` 单一身份源**（2026-05-18 重写）

user_id **只能**从 `request.state.user_id` 读，由 `auth_middleware` 在 handler 跑之前注入：cloud 模式来自 JWT decode，local 模式来自 `X-User-Id` header。没有任何 fallback——middleware 在 header 缺失时已经 401 了，handler 拿到的一定是用户明确声明的身份。

历史版本接受 query 参数 `?user_id=` 作为 backup，是为了 local 模式"少改前端"。结果是双 channel 并存、互相覆盖，写错 user_id 的 bug 在 2026-05-18 被踩到（详见 `backend/auth.py.md` 同日 entry）。这条 channel 现在彻底关闭。

**api_key 脱敏**

响应里的 api_key 被替换为 `"***" + 末4位`（`api_key_masked`），原始 `api_key` 字段被删除。这防止前端或日志意外暴露完整 key。

**添加提供商后立即热重载**

`add_provider` 和 `set_slot` 成功后会调用 `get_user_runtime_llm_configs` + `set_user_config` 来更新当前进程的 LLM 配置。用 runtime resolver 而不是旧三元组 resolver 的原因是 Codex agent 还需要把 `CodexConfig` 注入当前 task 的 ContextVar。这在 local 模式下有意义（单进程，热更新生效），在云模式多进程环境下实际上只更新了处理这次请求的进程，其他进程不受影响。注释说"Hot-reload for current process (local mode)"，但代码在任何模式下都执行，用 try/except 忽略了可能的失败。

**`claude-status` 豁免认证**

这个接口在 `backend/auth.py` 的 `AUTH_EXEMPT_PATHS` 里，不需要 JWT。原因是前端需要在登录之前就能检查 Claude Code CLI 状态（用于显示安装引导）。但在云模式下，它检查了 `request.state.role == 'staff'`，只允许 staff 使用 CLI——这个检查依赖中间件注入的 role，但由于豁免了认证，cloud 模式下 `request.state.role` 可能不存在，`getattr` 用了默认空字符串来避免 AttributeError。

**`claude-status` 返回字段：cli_installed / logged_in / email / expires_at**

email 和 expires_at 是 best-effort 解析——`claude auth status` 的 JSON
schema 在 minor 版本之间会变（有时把 email 放在顶层，有时放在
`account` / `user` / `profile`，有时根本没有；expiresAt 同样可能在顶层
或藏在 `token` / `oauth` / `credentials` 子对象里），所以代码逐一探测
常见 shape，解析不出来就保持 None。前端能渲染缺失字段（不显示而已），
所以这里宁可放过也不抛错。fallback 路径（直接读 `~/.claude/.credentials.json`）
也会顺手补一次 email/expires_at，覆盖 `claude auth status` 不可用或返回
不完整的 v1.x CLI 场景。

`claude auth status` 必须通过 async subprocess 执行，不能在 async FastAPI
handler 里用同步 `subprocess.run`。这个 CLI 在未登录、网络慢或自身卡住时会等到
10 秒超时；如果同步等待，会冻结整个 backend event loop，同一批 Settings 请求
里的 `/api/providers` 读 DB Proxy 也会被拖到超时，表现为 `httpx.ReadError` 和
500。

## Gotcha / 边界情况

- **`validate_slots` 检查所有 SlotName 但不校验提供商**：它只检查每个 Slot 是否配置了，不验证对应提供商的 API key 是否有效。真正的连通性测试用 `test_provider`。
- **`/slots/validate` 路径优先级**：这是 `GET /slots/validate`，需要在 `PUT /slots/{slot_name}` 之前注册（不同方法，实际上不冲突），但需要在 `GET /{provider_id}` 这类动态路径之前，否则 "slots" 会被当成 provider_id。FastAPI 在同一路由器内优先匹配更具体的路径，所以这里实际上没问题，但路径命名容易让人困惑。

## 新人易踩的坑

`/embeddings/rebuild` 和 `/embeddings/status` **都接受 `?user_id=...` 查询参数**
且必填。`EmbeddingMigrationService(db, user_id=user_id)` 按该 user 过滤所有
entity。`get_migration_progress(user_id)` 是 per-user 字典，用户 A 的 rebuild
不会阻塞用户 B 的状态查询或 rebuild。2026-04-20 之前这两个端点无 user_id 参数，
migration service 全局单例对云端多用户是错的。

多进程部署下，per-user 进度仍然是"当前处理这次请求的进程"内的状态；不同进程不
共享。前端轮询时若请求 load-balance 到不同进程，会看到进度波动。未来可考虑把
进度落到 DB 或 Redis，但本轮修复不包括。
