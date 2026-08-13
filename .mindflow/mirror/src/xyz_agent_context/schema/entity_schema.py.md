---
code_file: src/xyz_agent_context/schema/entity_schema.py
last_verified: 2026-08-13
stub: false
---

## 2026-08-13 — `UserStatus.BANNED`（账户停用专属状态值）

`UserStatus` 枚举新增 `BANNED = "banned"`。它是一个由运维设置的、独立的账户状态，
刻意与 `BLOCKED` / `DELETED` 分开，让账户停用机制（[[suspend.py]]）有自己的专属
值：`reinstate` 只需把行恢复成 `ACTIVE`，而 DB 里已存在的 `banned` 行也能正常
被枚举加载（不会在 enum 强制转换时报错）。middleware 的账户状态闸门把
`banned` / `blocked` / `deleted` 一并视为「不可交易」（见 [[auth]]）。

## 2026-08-04 — `is_agent_description_unset` + legacy 占位符常量

`LEGACY_AGENT_DESCRIPTION_PLACEHOLDER = "A new agent ready for configuration"`
是创建流程**过去**写进 `agent_description` 的填充串。现在创建写空串
（见 [[auth]]），但 prod 有约 488 行带着它，所以"没设置"必须同时认空和认这个
legacy 形态——`is_agent_description_unset()` 大小写与空白都不敏感。

它从来不是无害填充：bus 名录快照它，于是 `bus_get_agent_profile` 把配置好的
agent 报成"待配置"，询问方据此拒绝发消息（P1 段02，prod evt_feb1f6ae）；
[[basic_info_module]] 又把同一个字段当作 agent **自己的**自我描述注入系统提示，
所以被问的 agent 也这么认识自己。

**消费方的义务**：判定为 unset 时**什么都不要说**，绝不要把这句话打印出来——
对同伴复述"这是个待配置的新 agent"比留空更糟，它是在断言对方不可用。
三个消费面：[[message_bus_module]] 的 Known Agents 渲染、[[basic_info_module]] 的
自述、[[agent_discovery_sync]] 的名录写入。

## 2026-07-23 — AGENT_TEXT_MAX_LENGTH 常量

新增模块常量 `AGENT_TEXT_MAX_LENGTH = 255`,`Agent.agent_name` /
`agent_description` 的 `max_length` 都改成引用它。目的:让所有 agents 表写路径的
上限绑同一个来源——读模型(本文件)、`Create/UpdateAgentRequest`(api_schema)、
`ManyfoldCreate/UpdateAgentRequest`([[manyfold/agents.py]],走 raw db 写)、以及
bundle 导入修剪——不再各写字面量 255/200/2000 而漂移(NetMindAI-Open#71 就是写侧
绕过了这个上限)。前端另有一份镜像常量 [[agentLimits]]。行为不变,仍是 255。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-05-27 — `SocialNetworkEntity.embedding` field removed

Removed together with the rest of the social-network semantic-search
chain (Owner spec, scope B). The DB column `instance_social_entities.
embedding` stays on the table per iron rule #6 (no risky DB changes)
but no Python path reads or writes it any more. See [[social_network_module.py]]
and [[social_network_repository.py]] for the call-site removals.

# entity_schema.py

## Why it exists

This file consolidates four "core entity" domain models — `SocialNetworkEntity`, `User`, `Agent`, and `MCPUrl` — into one place. These are the objects that map directly to rows in the primary business tables (`instance_social_entities`, `users`, `agents`, `mcp_urls`). Centralizing them here means the repository layer and the route layer both import from a single canonical location rather than defining local versions.

## Upstream / Downstream

`SocialNetworkRepository` serializes/deserializes `SocialNetworkEntity`. `UserRepository` uses `User`. `AgentRepository` uses `Agent`. `MCPRepository` uses `MCPUrl`. On the API side, `api_schema.py` projects `SocialNetworkEntity` into `SocialNetworkEntityInfo` (a subset for the frontend) and `MCPUrl` into `MCPInfo`. The repositories are the only path to the database; the domain models here should never be written to the database by any other code.

## Design decisions

**`SocialNetworkEntity.instance_id` instead of `owner_agent_id`**: a refactoring in December 2025 changed the ownership model so that social network data follows a `SocialNetworkModule` *instance*, not directly an agent. This allows the same agent to have separate social graphs for different narrative contexts. Old code that tried to query by `agent_id` directly will silently miss records unless it first resolves the relevant `instance_id`.

**`SocialNetworkEntity.embedding` is stored inline in the entity row**: this was the original design. Later, `EmbeddingStoreRepository` was introduced as a normalized embedding store. For entities, there is now a dual-path: old vectors live in the `embedding` column, new vectors live in the `embeddings_store` table. `SocialNetworkRepository.semantic_search()` uses a bridge flag (`use_embedding_store()`) to choose which path to read from.

**`UserStatus.DELETED`** is a soft-delete marker, not a hard delete. The `UserRepository.delete_user()` method defaults to `soft_delete=True`, which just sets this status. The row stays in the database so foreign-key-like references in other tables remain valid.

**`Agent.is_public`** controls whether non-creator users can see and interact with an agent in the UI. This is an application-level visibility flag, not a database permission.

## Gotchas

**`MCPUrl` vs `MCPInfo`**: `MCPUrl` has `mcp_id`, `agent_id`, `user_id`, and the full connection state fields. `MCPInfo` in `api_schema.py` has all the same fields. The two are structurally identical by convention but are separate classes — changes to one do not propagate to the other automatically.

**`SocialNetworkEntity.tags` and `expertise_domains`** are both `List[str]` but they serve different purposes. `tags` are freeform descriptors used for keyword search (e.g., `"expert:recommendation_system"`). `expertise_domains` are normalized domain labels used for intelligent matching (e.g., `"recommendation_system"`). It is easy to put the same string in both by mistake; only `tags` is searched by `JSON_SEARCH` in `search_by_tags()`.

## New-joiner traps

- `Agent.agent_metadata` and `User.metadata` are both `Optional[Dict[str, Any]]` but stored as JSON strings in MySQL. `AgentRepository` and `UserRepository` each have their own `_parse_json_field()` static method to handle the conversion. Do not read these fields raw from a database cursor — always go through the repository.
- `SocialNetworkEntity.persona` is a natural language string (not structured data) describing how to communicate with this person. It is written by the agent during entity updates and read back into the system prompt context. Do not confuse it with `identity_info` which is structured JSON.
