---
code_file: src/xyz_agent_context/repository/cli_session_repository.py
last_verified: 2026-07-25
stub: false
---
# cli_session_repository.py — CLI 会话句柄数据访问

## 为什么存在

`agent_cli_sessions`(可 `--resume` 的 CLI 会话句柄,见 [[cli_session]])的
CRUD。resume 决策逻辑全在 runtime 侧(step_3 查表校验 / step_4 落库),这一层
只按唯一键三元组 `(agent_id, platform_session_id, framework)` 读写行。

## 上下游关系

被 step_4 的 4.7(upsert,fire-and-forget)调用;R2 起 step_3 的 resume 决策
调 `get`,R3 的失败兜底调 `delete_handle`。继承
`BaseRepository[AgentCliSession]`,`id_field="id"`(代理键——基类的按 id CRUD
基本不用,业务入口都是三元组方法)。

## 设计决策

**upsert 是两步 get→insert/update,故意不用原子 ON DUPLICATE KEY**:它只在
step_4 的 fire-and-forget 语境执行,竞态窗口无害——同 key 并发轮次后写覆盖
前写,恰是想要的语义(最新 CLI session 胜)。update 分支只刷新载荷列
(cli_session_id / config_fingerprint / working_path / narrative_id /
last_used_at + updated_at),代理键与 created_at 不动。

**删除叫 `delete_handle` 不叫 `delete`**:基类 `delete(entity_id)` 按代理键,
三元组签名覆写会类型不兼容(LSP);改名避开。
