---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_1_fast_select.py
stub: false
last_verified: 2026-08-06
---

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
