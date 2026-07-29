---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/session/turn_ledger.py
last_verified: 2026-07-29
stub: false
---
# session/turn_ledger — 回合唯一真相

三不变量构造保证(配对只能经 synthesize 收口/角色交替经步末折叠/seq 唯一分配)。step 文本+调用在 step_done 折叠成单条 assistant 消息(role 交替成立的机制)。compaction 登记 seq→消息替换,投影替换、日志留全史。resume=base 前缀续 seq。
