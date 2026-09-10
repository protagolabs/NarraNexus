---
code_file: src/narranexus/platform/repository/agent_circuit_breaker_repository.py
last_verified: 2026-09-10
stub: false
---
# agent_circuit_breaker_repository.py — 熔断器状态数据访问

## 为什么存在

`instance_agent_circuit_breaker`（每 agent 一行）的 CRUD。熔断器服务
（`agent_framework/loop/circuit_breaker.py`）拥有全部升级逻辑，这一层只读写行。

## 上下游关系

被 `agent_circuit_breaker` 服务和 `backend/routes/agents/circuit_breaker.py`（GET 状态）
调用。继承 `BaseRepository[AgentCircuitBreaker]`，`id_field="agent_id"`。

## 2026-09-10 — `try_claim_probe`：半开探测的 compare-and-swap（GitHub #117）

`try_claim_probe(agent_id, from_status, expected_probe_token, grant_until)` 用一条等值过滤
的 `UPDATE ... WHERE agent_id=? AND cb_status=? AND probe_token=?`（`probe_token` 为 None
时后端翻成 `IS NULL`）把 PAUSED 或过期的 PROBING 翻成 PROBING，写入**新随机** `probe_token`
并把 `cooldown_until` 重刻成 grant 到期；`rowcount > 0` 即"抢到"，返回新 token，否则 None。
不依赖任何应用层锁。

CAS 键必须是 `probe_token` 而不是 `cb_status`：stale-PROBING 自愈是 probing→probing，按
状态过滤对每个后来者都恒真（真 MySQL 实测 N 个并发全部"赢"）。`probe_token` 每次认领
都变、写回 PAUSED/COOLING/ACTIVE 时置 NULL，所以任何分支上它在认领前后都不同。

方言 gotcha：aiomysql 的 rowcount 是 **CHANGED** 行数（无 `CLIENT_FOUND_ROWS`）。这里安全
是因为 `probe_token` 必变；若有人把写入简化成"只改 cb_status"，probing→probing 分支在
MySQL 上会静默永远抢不到。`tests/agent_framework/test_agent_circuit_breaker_probe_mysql.py`
钉住这一点和 `IS NULL` 首次认领。

`cooldown_until` 在 PROBING 行上的含义是 grant 到期（服务层的三重语义之一）；`find_by_status`
供 `reset_for_owner` 拉出 paused/probing/cooling 行再按 owner 过滤。

## 设计决策

`upsert_state(agent_id, updates)` 是主力：按 agent_id 的**部分**写入（只动 updates 里的
键 + 刷新 updated_at），存在则 update，否则 insert（补 agent_id）。`find_by_status` 供
`reset_for_owner` 拉出 paused/probing/cooling 行再按 owner 过滤。`_row_to_entity` 直接
`AgentCircuitBreaker(**row)`——pydantic 忽略多余的 `id` 列、把 ISO 字符串/枚举串强制成
模型类型。
