---
code_file: src/xyz_agent_context/module/social_network_module/_entity_updater.py
last_verified: 2026-08-17
---

## 2026-08-17 — 记一个已知缺口：`should_update_persona` 的 change-signal 只认英文

本次**没改代码**，只是把一个原本只存在于一份已删除文件里的期望搬到有人会读的
地方。

`should_update_persona()` 判断"对话里出现显著变化信号"用的是写死的英文字面量
表（`"i changed my mind"` / `"budget changed"` / ...）。被删掉的
`src/.../social_network_module/test_persona.py` 里曾有一条用例断言中文也该触发
（`"我改变主意了……"`）——那条断言**从未通过过**（它调的方法早已重构到本文件，
而脚本从不被 `make test` 执行），所以这不是回归，是一个从未实现的期望；但它是
该期望唯一的书面记录，而产品对中文用户上线。

两个问题别混着修：(1) 非英文说法不触发重新推断，用户只会在 agent 继续按旧画像
说话时察觉，没有任何报错；(2) `"budget changed"` / `"decision process changed"`
是销售场景词汇，写死在通用模块里已经违反铁律 #4，**补一份中文表会让这个违规加
倍，不是修好它**。方向应是让本函数不再自带任何场景词表（交给 Awareness 声明，
或并入既有 LLM 判断），而不是加语言。

优先级不高的原因：每 10 轮的周期性刷新是保底路径，所以漏掉信号只是**延迟**重新
推断，不是永不推断。

守卫：`tests/social_network_module/test_persona_refresh.py`（只覆盖当前英文行为
+ turn-0 与大小写边界）。详细讨论在本地 `reference/self_notebook/todo/`
（该目录 gitignored，故此处留正本）。

## 2026-07-23 — meaningfulness guard (junk-entity filter)

`ExtractedEntity` gained `confidence` (default 1.0 so old outputs pass);
`is_meaningful_entity()` is the deterministic backstop applied inside
`extract_mentioned_entities` before create/merge: rejects generic
role/category names (en+zh set), bare system IDs / pure digits / uuid /
long-hex blobs, names > 80 chars, and confidence < 0.5. Rationale: the
prompt already forbids these but weak helper models leak them and every
leaked row is a permanent junk node in the graph (bug "entity 图无意义
条目"). Dropped entities are logged at INFO. Prompt gained a Confidence
section ([[prompts.py]]). Tests:
`tests/social_network_module/test_entity_filter.py`.
## 2026-06-10 — helper obtained via get_helper_sdk()

All five llm_function call sites switched to the protocol-agnostic
`get_helper_sdk()` factory (single-Claude-key users get the Messages-API
helper). Call shapes unchanged.


## 2026-05-27 — semantic-search 链路全删（Owner spec, scope B）

`update_entity_embedding`、`DEDUP_SIMILARITY_THRESHOLD`、`DEDUP_TOP_K` 都
被删了。Mentioned-entity 的 dedup 现在只剩 **Stage 1 (name/alias exact
match) → LLM 拍板 MERGE/CREATE**，不再做向量相似度检索 ("Bob" vs
"Robert" 异名同人会产生两条记录，后续靠人工/LLM 合并)。

同时 `get_embedding` import 也被删，文件不再依赖
agent_framework 的 embedding 工具（该子系统现已整体移除）。

为什么这么彻底——参见 [[social_network_module.py]] 的 2026-05-27 条目和
[[social_network_repository.py]] 的同期改动。

# _entity_updater.py — LLM 驱动的实体更新管道

## 为什么存在

从 `social_network_module.py` 分离出来（2026-03-06），把所有"需要调用 LLM 来更新实体信息"的逻辑集中维护。`social_network_module.py` 里的 `hook_after_event_execution` 只做编排——它调用这里的函数来完成摘要生成、描述追加、Persona 推断等具体操作。

核心操作（2026-05-27 修订）：
1. `summarize_new_entity_info`：从本次对话提炼新信息（LLM）
2. `append_to_entity_description`：追加到 `entity_description`，超长时自动压缩（LLM）
3. `update_interaction_stats`：递增 `interaction_count`，更新 `last_interaction_time`
4. `should_update_persona` / `infer_persona` / `update_entity_persona`：按条件触发的 Persona 推断（LLM）
5. `extract_mentioned_entities`：从对话中批量提取提及的其他实体（LLM）
6. `decide_merge_or_create`：dedup 阶段的 LLM 仲裁（MERGE / CREATE_NEW）

## 上下游关系

- **被谁用**：`SocialNetworkModule.hook_after_event_execution()` 按顺序调用这里的函数
- **依赖谁**：`OpenAIAgentsSDK.llm_function(output_type=...)`（所有 LLM 调用）；`SocialNetworkRepository`（DB 读写）；`prompts.py` 里的 LLM 提示词（`ENTITY_SUMMARY_INSTRUCTIONS` 等）

## 设计决策

**累积式描述，不覆盖**：`append_to_entity_description()` 把新摘要追加到现有 `entity_description` 末尾（带时间戳），而不是替换。这保留了历史信息。当描述超过某个长度阈值时，触发 `compress_description()`（LLM 压缩），把历史内容浓缩但保留关键事实，防止字段无限增长。

**批量实体提取**：`extract_mentioned_entities()` 分析对话里提及的第三方实体（"ta 提到了他的老板张三"），自动创建或更新这些周边人物的档案。这是被动的社交图谱扩张——不需要用户主动介绍，对话内容就能让 Agent 了解用户社交圈。

**`ExtractedEntity` Pydantic 输出 schema**：LLM 批量提取时输出 `BatchExtractionOutput`（含 `entities: List[ExtractedEntity]`），每个实体有 `name`、`entity_type`、`summary`、`tags`。使用结构化输出而不是自由文本解析，避免手工 parsing 的脆弱性。

**标签 dedup 上限 10**：新提取的 tags 在 merge 时做大小写不敏感的去重，并把每个实体的总标签数上限设为 10。这是为了防止 tag 膨胀导致语义漂移。

## Gotcha / 边界情况

- **`should_update_persona()` 的触发条件**：基于 `entity.interaction_count` 和 `final_output` 的长度，每隔 N 次或输出足够长时才触发。条件逻辑在这个函数里，不在 `social_network_module.py` 里——修改 Persona 更新频率就改这里。
- **批量提取的模糊匹配**：如果已有实体的精确 ID 匹配失败，会用实体名做 `keyword_search` 取前 3 条并选 `interaction_count` 最高的作为匹配项。这可能把不同的人错误归并（如同名用户）。

## 新人易踩的坑

- 这里所有 LLM 调用都是 `await` 的异步调用，但 `hook_after_event_execution` 本身是异步的且是 fire-and-forget 风格（见 `MemoryModule` 的类似模式）。如果 LLM API 超时，这里的错误会被上层的 try/except 静默捕获，实体更新失败但不影响主流程。
