---
code_file: tests/agent_runtime/test_im_dm_fallback_loop_guard.py
stub: false
last_verified: 2026-08-24
---
# test_im_dm_fallback_loop_guard.py — 兜底不能变成乒乓引擎

钉 [[step_3_agent_loop.py]] 新增的两道 DM 兜底前置门
（`agent_peer_no_fallback` / `fallback_rate_limited`）。

**第一条测试是基线**：人类 DM 照旧拿到兜底。这批改动最大的风险不是漏拦，
是把 0802 的修复顺手推翻。

**理由优先级也被钉住**：对端是 agent 但这轮已经有机发过回复 → 报
`already_replied_via_tool`（实际发生的事）而不是
`agent_peer_no_fallback`（我们本来也会拒绝的事）。报告要说最具体的那个。

计数器按 `channel:room_id` 分桶：一个吵闹的房间不能把别的房间也堵上；
拿不到身份时返回空 key 并**不计数**，否则所有无法识别的对话会挤进同一个
共享桶里互相饿死。
