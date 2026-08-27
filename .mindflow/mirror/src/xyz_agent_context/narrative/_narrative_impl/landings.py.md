---
code_file: src/xyz_agent_context/narrative/_narrative_impl/landings.py
last_verified: 2026-08-27
stub: false
---

# landings.py — 所有 decider 共享的 executor + Landing 值对象

## 为什么存在

review 2026-08-27 round 3(I3+M4):四个共享 executor(菜单/participant
候选构建、match/participant 落地)是"decider 换、executor 不换"的本体,
却散在 1,300 行的 retrieval 里;`Landing` 住在 merged_select 又迫使
flag-off 路径为一个 dataclass import 整条 helper-SDK 链。二者同迁于此。

## 坑

- `_candidate_labels` 是"候选给模型看什么"的**唯一定义**,随四个消费者
  同迁——绝不许在别处长出第二份(judge 两分支只修一份的历史)。
- 函数第一参数是 NarrativeCRUD(TYPE_CHECKING 引类型),无上行依赖。
- `CANDIDATE_DESC_MAX_CHARS` 随唯一消费者同住。
