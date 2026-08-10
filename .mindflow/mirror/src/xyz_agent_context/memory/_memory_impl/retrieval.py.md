---
code_file: src/xyz_agent_context/memory/_memory_impl/retrieval.py
last_verified: 2026-08-10
stub: false
---

## 2026-08-10 (PR-11) — grep_filter regex 路径改 ReDoS-safe 引擎（管线 blocked 修：offload + per-request 预算 + 截断信号）

`grep_filter` 的 `regex=True` 分支从 stdlib `re` 换成 **`regex` 包**（模块顶部 `import regex as _regex`，
别名因 `regex` 也是参数名；新增直接依赖）：pattern 是 agent/LLM 自报、不可信，stdlib `re` **不可中断**——
灾难性回溯 pattern 会钉死核，grep 上 HTTP 会 wedge 共享 API loop。`regex` 天然抗多数 ReDoS + `timeout=`
抛 `TimeoutError`（超时 record 跳过）。

**三个承重点（初版被管线判 Critical/Important 后补齐）**：
1. **offload**：CPU-bound 扫描不在共享事件循环上跑——[[engine]] `grep` 用 `run_in_executor` 把 grep_filter
   丢线程池（`regex` 匹配期释放 GIL，真 offload）。否则单进程 uvicorn 上一次同步扫描会卡住所有 HTTP+WS 流。
2. **per-request 预算**：`_GREP_REQUEST_BUDGET_S`(2.0s，原名误导性的 `_GREP_TOTAL_BUDGET_S` 已改名)。
   deadline 由 [[coordinator]] `grep_memory` **算一次**、经 engine.grep 传进来，全请求（跨 ~6 个 kind）共享
   一份预算，而非 budget×num_kinds。`_GREP_PER_MATCH_TIMEOUT_S`(0.25s) 是单条 record 上限。
3. **截断信号**：返回 `(hits, truncated)`。预算耗尽 break / 单条超时 continue 都置 `truncated=True` 并
   `logger.warning`（不是 debug——云端默认级别看不见 debug=等于没告警）。一路透到 tool/route 的
   `{"success":true, ..., "truncated":true}`，让 LLM 不把"扫到一半放弃"读成"我不记得"（错误负例比慢更糟）。
非法 pattern 仍回退子串（返回 `(hits, False)`）。安全界属**搜索原语**、非 agent-loop 上限（铁律 #14/#15 不适用）。
解锁 grep_memory 上 seam → general_memory mcp 容器弃 db 凭据。实测 tests/memory/test_grep_redos.py。

## 2026-06-08 — recall relevance gate + CJK stopwords

`rank_recall` now gates on keyword relevance: for a NON-blank query it ranks ONLY the BM25 hits (recency/proof/salience reorder WITHIN that set), so a zero-overlap record can no longer ride its recency boost into results — that was the cross-topic leak (an outdoor query pulling back finance records when a kind held few candidates). A blank query keeps the documented recency fallback; a non-blank query that matches nothing returns empty (no recency-dump). `tokenize()` also stopwords high-frequency CJK function chars (的/这/个/是/我…) that the per-character unigram tokenizer otherwise turned into BM25 terms; content-bearing borderliners (对/在/有/为/中/上/下/里…) are deliberately kept so a term like 对账 keeps full weight. Shared by narrative routing too — routing verified still 3/3 with unchanged scores. Tests: `tests/memory/test_recall_relevance_gating.py` (7 cases).

# retrieval.py — unified Agent Memory

Vector-free retrieval primitives: BM25-lite, grep, RRF fusion, recency/proof/salience boosts, token-budget trim. Pure functions over a bounded candidate set.

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.
