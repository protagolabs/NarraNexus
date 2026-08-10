---
code_file: src/xyz_agent_context/memory/_memory_impl/retrieval.py
last_verified: 2026-08-10
stub: false
---

## 2026-08-10 (PR-11) — grep_filter regex 路径改 ReDoS-safe 引擎

`grep_filter` 的 `regex=True` 分支从 stdlib `re` 换成 **`regex` 包**（新增直接依赖）：pattern 是
agent/LLM 自报、不可信，stdlib `re` **不可中断**——一个灾难性回溯 pattern（`(a|aa)+$` 类）会钉死一个核，
且 grep 一旦上 HTTP 会 wedge 共享 API loop。`regex` 包既天然抗多数 ReDoS，又支持 `timeout=` 抛
`TimeoutError`：超时的 record 当作不匹配跳过，另加 wall-clock 预算封顶**单次 grep_filter 调用**（一个 kind 的候选集，`_GREP_PER_MATCH_TIMEOUT_S`
0.25s / `_GREP_TOTAL_BUDGET_S` 2.0s）、超预算 log 截断（不静默）。**注意聚合**：coordinator.grep_memory 每个 memory kind（all_kinds() ~6-7）调一次 grep_filter，且 grep_filter 同步跑在 caller loop 上（kind 之间有 DB await 让出），故单次 grep_memory 请求最坏 ~num_kinds×2.0s CPU——有界、绝非 hang（远胜旧的不可中断 stdlib re），但若在意共享 loop stall，把 grep_filter 挪 run_in_executor 是 follow-up（todo）。非法 pattern 仍回退子串。
这是**搜索原语**的安全界，非 agent-loop 上限（铁律 #14/#15 不适用）。解锁 grep_memory 上 seam（HTTP 侧
不再拒 regex）→ general_memory mcp 容器可弃 db 凭据、RCE 收益兑现。实测见 tests/memory/test_grep_redos.py。


## 2026-06-08 — recall relevance gate + CJK stopwords

`rank_recall` now gates on keyword relevance: for a NON-blank query it ranks ONLY the BM25 hits (recency/proof/salience reorder WITHIN that set), so a zero-overlap record can no longer ride its recency boost into results — that was the cross-topic leak (an outdoor query pulling back finance records when a kind held few candidates). A blank query keeps the documented recency fallback; a non-blank query that matches nothing returns empty (no recency-dump). `tokenize()` also stopwords high-frequency CJK function chars (的/这/个/是/我…) that the per-character unigram tokenizer otherwise turned into BM25 terms; content-bearing borderliners (对/在/有/为/中/上/下/里…) are deliberately kept so a term like 对账 keeps full weight. Shared by narrative routing too — routing verified still 3/3 with unchanged scores. Tests: `tests/memory/test_recall_relevance_gating.py` (7 cases).

# retrieval.py — unified Agent Memory

Vector-free retrieval primitives: BM25-lite, grep, RRF fusion, recency/proof/salience boosts, token-budget trim. Pure functions over a bounded candidate set.

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
