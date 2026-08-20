---
code_file: src/xyz_agent_context/module/chat_module/_chat_writes.py
last_verified: 2026-08-20
stub: false
---

## 2026-08-20 — bootstrap 问候行的唯一写入方

问候语现在有**两个**写入触发点：`ChatModule.hook_persist_turn` 的懒 prepend（首轮历史为空时），
以及 `step_1` 的开局 seed（经 [[greeting_seed]] 判定后调用）。为避免「问候行长什么样」这份知识在
两处复制后漂移，行结构 + 时间戳约束集中在本文件（它和 [[_chat_reads]] 一样住在拥有
`instance_json_format_memory_chat` 表的 chat 模块里）。

`build_bootstrap_greeting_row(greeting, turn_started_at, instance_id, event_id=None)` 是问候行的
**唯一定义**。三条承重约束都在这里：
- `meta_data.bootstrap = True` 供前端 / auto-delete 识别。
- 时间戳锚在 **turn 起点（`Event.created_at`）减 1ms**，不是 `utc_now()`、也不是 agent 创建时间：
  - turn 起点 -1ms 严格早于用户第一条消息（戳的是 turn 起点）→ 升序渲染时问候排最前；
  - turn 起点 ≈ 用户按下回车的时刻 → 落在前端 `(role, content)+5min` session 副本去重窗口内
    （`buildTimeline.ts`）。锚得更早（如 agent 创建时刻）会冲破这个窗口，问候渲染两次。
- 序列化前把 naive datetime 归一成 aware-UTC，输出串带 `+00:00`，浏览器 `new Date()` 按 UTC 解析
  而非本地时区（MySQL 返回 naive datetime 的坑）。

`seed_bootstrap_greeting(...)` 幂等落库：实例已有任何消息就直接 no-op（和 hook 的
`len(messages)==0` 同一守卫）。所以它可以对主实例每轮调用 —— 新实例只 seed 一次，之后（或 hook
已写过）历史非空即跳过，问候永不翻倍、已有会话永不被重排。best-effort，失败退回 hook 兜底。
