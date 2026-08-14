---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_1_fast_select.py
stub: false
last_verified: 2026-08-14
---

## 2026-08-14 — durable miss 先复用 session 线程再创建（复核 N1）

小语料 BM25 退化（逐字重复的 query 都可能低于 floor——floor 是噪声滤网
不是强度测试，见 narrative/config.py），miss 即创建会把一场对话打碎成
每 turn 一条 narrative（各带独立 ChatModule instance = 各自的历史，用户
体验为失忆）。修：durable miss 时若 `session.current_narrative_id` 在
`FAST_REUSE_WINDOW_S`（30 分钟）内被触碰过则单次 load 复用
（retrieval_method="session_fast"），行不通才 create_fast。naive 时间戳
按 UTC 处理（与 continuity.py 同规则）。ephemeral（voice）不受影响。
真 service 层锁在 tests/narrative/test_fast_path_service.py（create_fast
持久化 + 小语料 miss 现实性）。

## 2026-08-14 — durable 模式：miss 建 narrative + session 锚定（预审 C1/I4）

签名增可选 `session_service=None`；「结构上不可能写 session」的旧契约改为
**行为契约**（ephemeral profile 零 session 触碰，测试锁死）。
`profile.narrative_persistence == "durable"` 且 `_is_user_chat` 时：
miss → `narrative_service.create_fast`（纯 CRUD，retrieval_method=
"bm25_fast_created"）；hit/created → 镜像 full select() 的四个 session 写
（last_query / current_narrative_id / query_count / last_query_time）并
save——否则下一个非 fast turn 的 continuity 会拿到半新不旧的锚
（step_4 只动 last_response）。voice 行为逐字节不变。

## Why it exists

F28 快速模式下 step_1 的替身：一次 BM25 top-1 直取代替「ContinuityDetector
LLM 判定 + 检索 LLM 层 + 可能的新建」。设计立场是**便宜地保留 narrative**
而非绕开它——命中即注入背景，未命中裸跑，绝不新建。

## Design decisions

- **结构性禁止 session 写入**：签名根本不收 session_service（测试
  `test_signature_has_no_session_service` 钉住）。语音 turn 结束后，普通
  消息的连续性判定行为与语音 turn 从未发生过完全一致。
- **必须保住 ChatModule instance 不变量**：历史装配与 turn 持久化都挂在
  「用户在选中 narrative 下的 ChatModule instance」上，故命中路径复用
  step_1 的 `_ensure_user_chat_instance`；ensure 失败降级（有背景无实例）
  而不是死 turn。
- 检索文本偏好与 full 路径一致：trigger 的 retrieval_anchor 优先于完整
  execution prompt。
- run() 侧同分支跳过 step_1_5（markdown 只喂 instance-decision LLM，而
  skip_module_decision_llm 默认已绕过它）。

## Downstream

`agent_runtime.run()` 在 `turn_profile.narrative_strategy == "bm25_top1"`
时调用；服务侧对应 `NarrativeService.select_fast`。测试：
tests/agent_runtime/test_step_1_fast_select.py + tests/narrative/test_select_fast.py。
