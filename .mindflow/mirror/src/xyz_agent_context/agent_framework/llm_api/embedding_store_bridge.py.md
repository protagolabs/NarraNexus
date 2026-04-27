---
code_file: src/xyz_agent_context/agent_framework/llm_api/embedding_store_bridge.py
last_verified: 2026-04-20
stub: false
---
# embedding_store_bridge.py — 向量持久化的统一入口

## 为什么存在

统一的 `embeddings_store` 表按 `(entity_type, entity_id, model)` 存所有向量，
支持多模型、多 entity type、多租户共存。Bridge 把"该存哪 / 怎么读"的决策集中
在一处，所有模块（narrative、job、entity…）通过 `store_embedding()` /
`get_stored_embedding()` 调用而不直接依赖 repository 层。

## 上下游关系

上游调用者：所有需要持久化或读取向量的模块（`narrative` 包的向量操作、`job_module` 的语义存储等）。调用方只需 `store_embedding("narrative", nar_id, vector)` 和 `get_stored_embedding("narrative", nar_id)`，不用知道底层 `EmbeddingStoreRepository` 的存在。

下游：`EmbeddingStoreRepository`（通过 lazy import 避免循环依赖），读取 `api_config.embedding_config.model` 来识别当前模型，确保同一 entity 的不同模型向量可以共存于表中。

`use_embedding_store()` 现在无条件返回 True —— 见下方设计决策。

## 设计决策

**`use_embedding_store()` 无条件 True**（2026-04-20 改，Bug 11 真相之一）：
早期的 `llm_config.json` file-existence gate 在云端一律返回 False（云端所有
provider 在 `user_providers` DB 表里而非全局 JSON 文件），导致云端所有向量
读都落到 legacy routing_embedding 列，多用户多模型场景被 last-write-wins
污染。dual-write 从表建立之初就运行，`embeddings_store` 一直在被可靠填充，
因此切换 gate 是纯粹的"读路径升级"。空查询（用户新模型还没向量）由
`EmbeddingBanner` / `EmbeddingStatus` 前端 UI 自动提示用户做 per-user rebuild。

**非致命性错误**：所有函数的异常都被 `try/except` 捕获后 log warning 并返回
`None`/`{}`，而不是抛出。向量存储失败不应该中断主流程（agent 回答用户问题的
流程）。这是有意识的降级策略。

**lazy import 避免循环依赖**：`_get_repo()` 在调用时才 import
`EmbeddingStoreRepository` 和 `get_db_client`，而不是模块加载时 import。这
避免了 `agent_framework` → `repository` → `utils` → `agent_framework` 的
潜在循环。

**model 维度不固定在 EmbeddingConfig.dimensions**：读取向量时用
`embedding_config.model` 作为 key 查找，同一 entity 可以同时有多个不同 model
的向量。用户切换 embedding model 后旧向量不会被删除，只是新向量用新 model 存。

## Gotcha / 边界情况

- `store_embedding` 的 `source_text` 最多截断到 2000 字符存储，这只是辅助信息
  （用于未来重新 embed），不影响向量本身。
- 翻转 gate 后，`narrative.routing_embedding` / `module_instances.routing_embedding`
  legacy 列仍然被 write path 写入（dual-write），但 read path 不再读它们。下个
  清理 window 可以把这两列的写删掉（不在 Bug 11 scope）。

## 新人易踩的坑

- 调用 `get_stored_embedding()` 返回 `None` 可能是两种情况：embedding 不存在，或者存储读取失败。两者都被 log warning + 返回 None，调用方无法区分。需要先看日志。
- `use_embedding_store()` 的历史是"先双写，再翻转读路径"。若未来还需新增向量后端
  （例如切 pgvector），应继续保留 gate 的形式而不是硬 return True——便于下一次
  渐进切换。
