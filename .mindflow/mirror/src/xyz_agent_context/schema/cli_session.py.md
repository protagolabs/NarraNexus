---
code_file: src/xyz_agent_context/schema/cli_session.py
last_verified: 2026-07-25
stub: false
---
# cli_session.py — 可 resume 的 CLI 会话句柄数据模型

## 为什么存在

agent_loop resume 化(计划
`reference/self_notebook/plans/2026-07-25-agent-loop-resume.plan.md`)的数据
契约:一行 `agent_cli_sessions` = 一个平台会话对应的可 `--resume` CLI session。
E1 实验证明 CLI `--resume <session_id>` 跨进程可用且轮间缓存真实命中;这个模型
承载那个句柄及其三个有效性锚。

## 上下游关系

表定义在 `utils/db/schema_registry.py`(agent_cli_sessions,唯一键三元组
`agent_id + platform_session_id + framework`);读写经
[[cli_session_repository]]。写方:step_4 的 4.7(捕获);读方(R2 起):step_3
的 resume 决策。`platform_session_id` 对应 `ConversationSession.session_id`
(平台 Session 是文件存储,不是 DB 表——所以句柄必须独立开表)。

## 设计决策

**三个有效性锚,任一不符 → 冷启动**:`narrative_id`(narrative 切换 = 话题域
变了,新 CLI session 是规则不是缺陷)、`config_fingerprint`
(`ClaudeConfig.resume_fingerprint()`,provider/模型/auth/config dir 任一变化)、
`working_path`(CLI 按启动 cwd 的 slug 归档 session jsonl)。resume 是优化,
永远不是正确性依赖——查不到/不匹配就回落到今天的全量历史冷启动。

**id 是代理键**(auto-increment,insert 前 pop 掉);`created_at`/`updated_at`
Optional 并在写入时 pop,由 DB default 填。`last_used_at` 默认 `utc_now`。
