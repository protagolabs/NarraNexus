---
code_file: src/xyz_agent_context/narrative/_narrative_impl/_retrieval_llm.py
last_verified: 2026-08-12
stub: false
---
## 2026-08-12 — 候选渲染出 BM25 证据；`llm_confirm` 死代码簇删除

判官 prompt 的 search 候选块现在多两行：`Matched terms:`（按贡献降序，最多 5 个）
和 `Matched content:`（高贡献词在原文里的上下文）。写入侧的完整推理见
[[retrieval.py]] 的 2026-08-12 条目 —— 这里只记本文件的两个后果：

1. `if candidate.get('matched_content')` **不再是死分支**。它 2026-03-06 随本文件
   从 retrieval.py 拆出来时是活的，写入侧 2026-04-15 在**另一个文件里**被删，于是
   它走了将近 4 个月的 else。else 现在是 `logger.warning`：search 候选必然来自
   BM25、必然至少命中一个词，snippet 不可能合法为空 —— 它再响就是接线又断了。
   （原来成功路径上那条 `logger.info` 删了：它唯一的用途是"确认分支是否活着"，
   现在由测试钉住，留着只是每轮每候选的噪声。）
2. `llm_confirm` / `NarrativeMatchOutput` / `RelationType` 删除（铁律 #2，不留兼容
   别名）。它们和 `retrieval._prepare_candidates` / `_llm_confirm` /
   `prompts.NARRATIVE_SINGLE_MATCH_INSTRUCTIONS` / `prompts_index` 的索引项构成一个
   **互相调用但零外部入口**的闭环 —— docstring 里写的调用者 `retrieve_or_create`
   早已不存在。`_prepare_candidates` 还是候选标签逻辑的**第三份拷贝**（也在读已
   冻结的 `topic_hint`），而一份死掉的拷贝正是下一次漂移的起点。
   `tests/narrative/test_judge_match_evidence.py` 按名字钉住"这些东西不存在"。

# _retrieval_llm.py — Narrative 匹配判定的纯 LLM 逻辑

## 为什么存在

从 `retrieval.py` 抽出来的**纯 LLM 判定函数**，不依赖 `NarrativeRetrieval` 的任何
状态。负责回答"用户这条 query 该归到哪个已有 Narrative，还是新开一个话题"：

- `llm_judge_unified` — 多候选统一判定，同时权衡 BM25 search 结果、default Narrative、
  和 PARTICIPANT Narrative（用户是参与者的话题）。命中优先级：participant → default
  → search。**这是全流水线唯一的语义检查。**

输出用 Pydantic（`UnifiedMatchOutput`）强约束，对 `matched_index` 做越界检查，越界
则降级为"无匹配 / 新话题"而不是崩。LLM 调用整体包在 try/except 里，失败返回
`matched_id=None`——Narrative 路由宁可多开一个话题也不能因为一次 LLM 抖动炸掉主流程。

上游：`retrieval.py` 的 Narrative 选择流程。判定结果决定 Instance 绑到哪个 Narrative。

## 2026-06-17 — LLM 调用切到 protocol-agnostic 的 get_helper_sdk()

PR #25 把两处 `OpenAIAgentsSDK()` 直接实例化改成 `get_helper_sdk()`（`llm_confirm` 与
`llm_judge_unified` 各一处）。意图与全仓一致——judge 用的 helper LLM 不绑死在 OpenAI
Agents SDK 上（铁律 #9），底层可换而本文件不动。`model` / `reasoning_effort` 仍取自
`config.NARRATIVE_JUDGE_LLM_*`，调用契约不变，无判定逻辑改动。
