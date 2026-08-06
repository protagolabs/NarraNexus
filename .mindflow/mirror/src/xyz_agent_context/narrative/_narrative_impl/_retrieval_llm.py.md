---
code_file: src/xyz_agent_context/narrative/_narrative_impl/_retrieval_llm.py
last_verified: 2026-08-06
stub: false
---

## 2026-08-06 — 两个判定函数 opt into latency_sensitive

`llm_confirm` 与 `llm_judge_unified` 传 `latency_sensitive=True`。它们是 step.1 的 setup_s 热路径（prod 实测 unified match 平均 13s / 最大 44s，全在 DeepSeek-V4-Flash 思考上）。换模型的机制与三重门在 [[openai_agents]] `_latency_swap_model`；本文件只声明调用性质。

# _retrieval_llm.py — Narrative 匹配判定的纯 LLM 逻辑

## 为什么存在

从 `retrieval.py` 抽出来的一组**纯 LLM 判定函数**，不依赖 `NarrativeRetrieval` 的任何
状态。负责回答"用户这条 query 该归到哪个已有 Narrative，还是新开一个话题"：

- `llm_confirm` — 单候选二元确认，`retrieve_or_create` 用。`continuation` /
  `reference` 都算命中。
- `llm_judge_unified` — 多候选统一判定，同时权衡 BM25 search 结果、default Narrative、
  和 PARTICIPANT Narrative（用户是参与者的话题）。命中优先级：participant → default
  → search。

输出用 Pydantic（`NarrativeMatchOutput` / `UnifiedMatchOutput`）强约束。两个函数都对
`matched_index` 做越界检查，越界则降级为"无匹配 / 新话题"而不是崩。LLM 调用整体包在
try/except 里，失败返回 `matched_id=None`——Narrative 路由宁可多开一个话题也不能因为
一次 LLM 抖动炸掉主流程。

上游：`retrieval.py` 的 Narrative 选择流程。判定结果决定 Instance 绑到哪个 Narrative。

## 2026-06-17 — LLM 调用切到 protocol-agnostic 的 get_helper_sdk()

PR #25 把两处 `OpenAIAgentsSDK()` 直接实例化改成 `get_helper_sdk()`（`llm_confirm` 与
`llm_judge_unified` 各一处）。意图与全仓一致——judge 用的 helper LLM 不绑死在 OpenAI
Agents SDK 上（铁律 #9），底层可换而本文件不动。`model` / `reasoning_effort` 仍取自
`config.NARRATIVE_JUDGE_LLM_*`，调用契约不变，无判定逻辑改动。
