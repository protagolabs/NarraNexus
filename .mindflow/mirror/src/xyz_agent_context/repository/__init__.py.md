---
code_file: src/xyz_agent_context/repository/__init__.py
last_verified: 2026-08-13
stub: false
---
## 2026-08-13 — 导出 `BanAuditRepository`

账户状态变更审计（`ban_audit` 表）的数据访问进公共导出面（import + `__all__`）。
纯转发改动，无行为变化。见 [[ban_audit_repository]]。

## 2026-08-05 — 导出 `AgentRegistryRepository`

`bus_agent_registry`（同伴发现名录）的数据访问进公共导出面。该表原先由三处
内联代码各写各的（bus 模块每轮钩子、`bus_register_agent` 工具、
`InstanceFactory._register_agent_in_bus`），现在收敛成
[[agent_registry_repository]] + [[agent_discovery_sync]] 单点策略，调用方从包门面
import 即可。纯转发改动。

## 2026-07-29 — 移除 `CliSessionRepository` 导出

`cli_session_repository.py` 随 T7 删除:它 CRUD 的 `agent_cli_sessions` 表已摘掉
注册(见 [[schema_registry]]),而句柄机制整体被"每轮自建 transcript"取代
(见 [[transcript]])。纯转发改动。

# repository/__init__.py — repository 包的集中导出门面

## 2026-07-25 — 导出 CliSessionRepository

新增 re-export `CliSessionRepository`（可 resume 的 CLI 会话句柄数据访问,表
`agent_cli_sessions`）。纯导出改动,无行为变化。见
[`cli_session_repository.py`](cli_session_repository.py.md)。

## 为什么存在

集中 re-export 数据访问层的所有 Repository（Event / Narrative / SocialNetwork / Job /
Inbox / Agent / AgentMessage / MCP / User / Instance / Team / SkillArchive 等,都继承
`BaseRepository`），让别处统一 `from xyz_agent_context.repository import XxxRepository`,
无需记住每个 repo 住在哪个子文件。也顺带 re-export 几个常用 schema 实体（`Agent` /
`User` / `MCPUrl` 等)方便调用方。新增 repo = 在这里加一行 import + 补进 `__all__`。

## 2026-07-13 — 导出 AgentCircuitBreakerRepository

新增 re-export `AgentCircuitBreakerRepository`（实时层 Agent 熔断器状态的数据访问,表
`instance_agent_circuit_breaker`）。纯导出改动,无行为变化。见
[`agent_circuit_breaker_repository.py`](agent_circuit_breaker_repository.py.md)。

## 2026-08-11 — 导出 `TeamBulletinRepository`

见 [[team_bulletin_repository]]。