---
code_file: src/xyz_agent_context/bootstrap/greeting_seed.py
last_verified: 2026-08-20
stub: false
---

## 2026-08-20 — 「该不该 seed 问候语」的判定（门禁必须对齐 hook 的 bootstrap_active）

`resolve_bootstrap_greeting_to_seed(db, agent_id, user_id)` 回答**决策**：这个 (agent, user)
的 bootstrap 问候语要不要落库、落哪条文本 —— 返回 greeting 文本或 `None`。真正的 chat 行写入交
给唯一写入方 [[../module/chat_module/_chat_writes]]（`seed_bootstrap_greeting`），它持有行结构 +
时间戳约束；本模块刻意不碰 chat 表。

**门禁必须和 hook 一致，不能只看 metadata**：`agent_metadata["bootstrap_greeting"]` 在 provision
时写一次、**永不清除**。若只用它当门禁，agent bootstrap 过期后每开一个**新 narrative** 都会得到
一个全新的空 chat 实例，而写入方的幂等是**每实例**的 —— 于是过时问候语会被塞进此后每一个新
narrative 的首条，永久重复。所以 `resolve_*` 复算了和 `context_runtime` 完全相同的
`bootstrap_active`：
- **owner-only**：`agent.created_by == user_id`（context_runtime 只在 owner 轮注入 Bootstrap）。
- **Bootstrap.md 仍在**：用**读侧** `resolve_existing_workspace`（不是写侧 resolver —— 后者在
  legacy flat workspace 上找不到文件会静默停 seed，还会和 hook 分歧）。
- **未过阈值**：`event_count < auto_delete_threshold_from_meta(metadata)`（events 表 COUNT；
  threshold=None 表示语义型 profile 永不自动删，恒 active）。

`_bootstrap_active(db, agent_id, user_id, metadata)` 是这段逻辑的可测 helper（测试 patch 它 /
patch `resolve_existing_workspace`，不用全局 patch `os.path.isfile`）。

**上游**：`step_1_select_narrative`，选完 narrative 后仅对 **head（`narrative_list[0]`）** 实例调用
一次（问候语作用域是 (agent, user)，不是 per-narrative）。fast-select / step_4 路径靠
[[chat_module]] 的 `hook_persist_turn` prepend 兜底。已知边缘：若同一 bootstrap 轮内 agent 路由到
新 narrative，hook 会再问候 rebind 后的实例（详见 [[step_1_select_narrative]] 注释）。

全程 best-effort：任何异常返回 `None`，hook prepend 仍是兜底。
