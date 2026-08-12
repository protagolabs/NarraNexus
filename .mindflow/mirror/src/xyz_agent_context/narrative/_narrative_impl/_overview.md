---
code_dir: src/xyz_agent_context/narrative/_narrative_impl/
last_verified: 2026-08-12
stub: false
---

# _narrative_impl/ — Narrative 服务的私有实现层

## 目录角色

这是 `narrative/` 包的内部引擎室，不对外导出。所有外部调用都经过 `NarrativeService` 门面，`_narrative_impl/` 的类不能被包外代码直接实例化（名称前缀 `_` 就是这个约定）。

九个文件各司其职：数据库 CRUD、候选召回、数值闸门、LLM 判定、后台摘要更新、连续性
检测、默认 Narrative 管理、Instance 依赖处理、Prompt 构建。这种细粒度切分是为了让每个
文件足够专注，可以独立修改而不影响其他部分。

## 关键文件索引

（2026-08-12 核对过：表格与目录逐一对齐。原表列的 `vector_store.py` 已随
2026-06-09 unified-memory 重构删除 —— 向量全部退役，召回是纯 BM25；`routing_gate.py`
是 2026-07-29 新增的，原表缺。）

| 文件 | 职责 |
|------|------|
| `crud.py` | Narrative 的数据库读写，不含业务逻辑；`_index_narrative` 投影进统一记忆索引 |
| `retrieval.py` | BM25 候选召回 + participant 通道 + 组装判官候选；决定"属于哪条线" |
| `routing_gate.py` | 纯函数数值闸门（raw floor + top1/top2 margin），决定要不要跳过判官 |
| `_retrieval_llm.py` | 判官那次 helper LLM 调用（unified match，带 / 不带 PARTICIPANT 两版） |
| `updater.py` | Event 结束后由 helper LLM 全量重写 name / current_summary / topic_keywords |
| `continuity.py` | 判断当前 query 是否仍属于 session 里上一轮那条 Narrative |
| `instance_handler.py` | Instance 完成时处理依赖链，激活 blocked Instance |
| `default_narratives.py` | 系统预置的 8 个默认 Narrative 的定义和初始化逻辑 |
| `prompt_builder.py` | 把 Narrative 序列化成 LLM prompt 片段（稳定半 / turn 半） |
| `prompts.py` | LLM 调用的静态 prompt 模板 |

> `default_narratives.py` 与 `instance_handler.py` **还没有 mirror md**（本次未补：
> 没读过它们，写出来会是凑数的文档）。谁下次动这两个文件，按铁律 #10 顺手补上。

## 和外部目录的协作

**向上暴露**：通过 `_narrative_impl/__init__.py` 导出 `NarrativeCRUD`、`NarrativeRetrieval`、`NarrativeUpdater`、`InstanceHandler`、`PromptBuilder`、`ContinuityDetector`，供 `NarrativeService` 消费。

**外部依赖**（2026-08-12 核对）：
- `retrieval.py` 依赖 `memory/_memory_impl/retrieval.py` 的 `bm25_explain` / `bm25_snippet`
  （**路由与记忆召回共用同一份 BM25**），`_retrieval_llm.py` 的判官函数，
  `routing_gate.py` 的闸门。EverMemOS 已解耦，只剩几处 `# evermemos_memories removed` 注释
- `updater.py` 依赖 `narrative/config.py` 的 `config.NARRATIVE_LLM_UPDATE_INTERVAL`；
  embedding 相关依赖已随 2026-06-09 重构移除
- `continuity.py` 依赖 `agent_framework/adapters/openai_agents.OpenAIAgentsSDK` 做结构化 LLM 调用，并与 `channel/channel_context_builder_base.py` 的 Matrix 模板格式有隐式耦合（`_extract_core_content()` 函数）
- `instance_handler.py` 被 `services/module_poller.py` 直接从 `narrative` 包导入使用
