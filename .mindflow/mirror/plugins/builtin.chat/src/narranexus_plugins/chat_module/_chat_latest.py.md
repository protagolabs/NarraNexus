---
code_file: plugins/builtin.chat/src/narranexus_plugins/chat_module/_chat_latest.py
last_verified: 2026-10-30
stub: false
---

# _chat_latest.py — 一批 agent 的对话 tab 最新一行（/chat-latest 的实现）

## 为什么存在

侧栏后台「有新回复」原来每 15s 对每个 agent 调一次 simple-chat-history(include='chat')，请求数
随 agent 数线性增长。`chat_latest_for_user(db, user_id, agent_ids)` 一次给出整批（≤200）答案：
每个 agent 在**调用者自己的** ChatModule instance 里、对话 tab 会显示的最新一行的原始时间戳。

## 与 tab 一致（按构造）

- 同一个投影 [[_chat_projection]] `project_message`（peer_scoped=False）；activity 行与无时间戳的
  行不算。
- 平局：instance 内取后出现的消息、跨 instance 按 `agent_id, created_at DESC` 顺序取后者，与 tab
  的稳定排序 + 前端取末条一致。
- 作用域：`user_id = 调用者`、ChatModule、排除 cancelled/archived，不拉 peer instance（同
  include='chat'）。测试直接拿真实 simple-chat-history 对拍。

## 成本与缓存

一次 `InstanceRepository.list_ids_by_agents_and_user`（走 `(agent_id, user_id)` 索引、只投影三列）、
一次只取 `(instance_id, updated_at)` 的读；只有 `updated_at` 变了的行才整读 memory 并解析，每次
最多 20 行一批（memory blob 只增不减，冷启动不一次拉 N×MB）。进程内 LRU 缓存 `_LATEST_CACHE`
（上限 2 万）是纯缓存，各进程独立正确；本次调用的结果收进局部字典，并发调用淘汰缓存也不会让
某个 agent 从这次响应里消失。

**只信任亚秒精度的 `updated_at`**（`stamp_is_precise`）：MySQL DATETIME(6) 带微秒（2026-10-30 已
核实 dev / prod 的该列都是 `datetime(6)`、值带微秒）；SQLite 的 `datetime('now')` 只有整秒（后端解析成
microsecond=0 的 datetime），同一秒两次写入 `updated_at` 不变——整秒时间戳每次重读（本地单用户）。

依赖的写入口：`EventMemoryRepository.add_instance_json_format_memory` 的 upsert 把 `updated_at`
刷成 `CURRENT_TIMESTAMP(6)`；bundle importer 插新行（列默认值）；wipe / 删号删行。新增写入口若直接
UPDATE memory 而不刷 `updated_at`，缓存会过期。

## 容错

单个 instance 的 memory 解析失败（列表形 blob、非 dict 消息、`meta_data: null` 等）只记 warning、
该 instance 记为「无行」并缓存，**不**让整批返回失败——否则一行坏数据会让最多 200 个 agent 的
提醒每个 tick 都失效。失败方式与 tab 一致：tab 的循环按顺序 append，遇到第一条读不了的消息就中断、保留之前的行；
这里的折叠也停在同一处、保留之前的结果（不会出现「有提醒、tab 里却没有新行」）。warning 按 (instance, updated_at)
去重，本地整秒时间戳每次重读时不会每个 tick 刷日志。
