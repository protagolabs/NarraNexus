---
code_file: scripts/migrate_users_to_netmind.py
last_verified: 2026-06-11
stub: false
---

# migrate_users_to_netmind.py — 存量用户 → NetMind userSystemCode 一次性迁移

## 为什么存在

Phase 1 用户系统统一后，`users.user_id` 的语义从「自造用户名」变为 NetMind 的 32-hex userSystemCode。存量内测用户的旧 id 散布在 19 个身份列 + workspace 目录名里，需要一次性改写。**绝不放进 backend lifespan**（v1.7.16 事故：耗时迁移跑超 compose healthcheck 窗口导致 prod 下线 20 分钟）——本脚本只能停服离线跑（`make app-down` 之后）。

## 三段式工作流

1. `--report`：盘点全部用户，email 解析链 = `users.email`（旧注册流不写）→ `invite_codes.used_by_user_id → email`（唯一的映射来源，所以 invite_codes 表删功能不删数据）；无 email 的输出 issue 清单人工决断。
2. `--execute --mapping m.csv`：CSV（old_user_id,new_user_system_code，由 NetMind 批量建号回传）逐用户迁移：单事务改写所有身份列 → users.metadata 盖 `netmind_migration` 戳（存 old id，审计用）→ 事务提交后改名 workspace 目录 `{agent_id}_{old}` → `{agent_id}_{new}`。
3. `--verify --mapping m.csv`：逐列 COUNT 旧 id 残留，非零即退出码非 0。

## 设计决策

- **列清单运行时派生 + 显式分类校验**：从 `schema_registry.TABLES` 扫描身份形名字的列（user_id/owner_user_id/created_by/used_by_user_id/scope_id），任何未分类的新列直接 raise——加新表的人必须有意识地分类，不能被静默漏掉。
- **显式排除表**：`channel_slack_credentials.owner_user_id` / `channel_telegram_credentials.owner_user_id` 是 IM 平台 uid；`bus_channels.created_by` 是 **agent id**（message_bus_trigger 的 owner 激活规则）。
- **memory_\* 条件改写**：`scope_id` 只在 `scope_type='user'` 时是用户 id（八张 memory 表统一处理）。
- **幂等**：迁移以「旧 id 的 users 行还存在」为门槛，重跑自动跳过；目录改名容忍已改名。崩溃恢复 = 重跑一次。事务提交后、目录改名前崩溃的情况由第二遍 rename pass 兜底（按 new id 反查 agents）。
- **BINARY 比较**：与 UserRepository 一致，user_id 大小写敏感。

## 测试

`tests/migrations/test_migrate_users_to_netmind.py`：分类完整性（drift 守卫）、report email 解析、execute 全链路（含排除列不动、agent-scope memory 不动、目录改名、metadata 戳）、verify 残留计数、幂等重跑。
