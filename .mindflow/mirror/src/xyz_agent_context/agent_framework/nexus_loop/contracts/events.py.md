---
code_file: src/xyz_agent_context/agent_framework/nexus_loop/contracts/events.py
last_verified: 2026-07-29
stub: false
---
# contracts/events — 两轨事件与账本条目类型

track=model(重建上下文)/ui(仅前端重放)是恢复/回放/压缩一切能力的前提(C1)。entry 即 schema:LedgerEntry 与未来 nexus_events 行同形,落库换 writer 不改类。压缩是追加 TYPE_COMPACTION replacement 条目,不是删除。payload 形状用 TypedDict 声明(R2),金样测试锁定。Usage.as_legacy_dict 同时给出双词汇 cache 键(遗留消费两种拼法都探)。Phase 固定枚举是正式决策(R4),伪相位走 hook。
