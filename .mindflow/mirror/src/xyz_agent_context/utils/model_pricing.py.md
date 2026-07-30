---
code_file: src/xyz_agent_context/utils/model_pricing.py
last_verified: 2026-07-30
stub: false
---

# model_pricing.py — 每 token 美元定价的唯一解析点

## 为什么存在

从 [[cost_tracker]] 里搬出来的。它原本内联着一张手写价目表：

```python
MODEL_PRICING = {
    "gpt-5.1-2025-11-13": {"input": 2.0,  "output": 8.0},
    "gemini-2.5-flash":   {"input": 0.15, "output": 0.60},
}
```

两条。2026-07-30 查线上库时发现，**实际在跑的 7 个 model 一条都没命中** ——
`haiku`(207 行)、`minimax/minimax-m2.5`(1416 行)、
`deepseek-ai/DeepSeek-V4-Flash`(351 行)、`gpt-5`、`gpt-5.4-mini` 全部落空。
后果是 `llm_function` / `llm_stream` / `embedding` 三类共 2254 次调用、374 万
输入 token，`total_cost_usd` 全是 0。`agent_loop` 之所以有钱数，只是因为它走
`sdk_cost_usd` 完全绕开了这张表。

而且**唯一命中的那条本身也是错的**：`gemini-2.5-flash` 写的 0.15/0.60，实价是
0.30/2.50。没人回头维护的两行 dict 不是价目表，是让成本"看起来被度量了"的装饰。

## 数据源选择

改用 `litellm.model_cost`（~2983 条，上游维护，本仓已依赖 —— 见
[[litellm_client]]），关键是它带 prompt cache 三档
(`cache_creation_input_token_cost` / `cache_read_input_token_cost`)，这正是
Anthropic 形状的账单需要的。铁律 #9 的考虑：litellm 只当**数据源**用，整个耦合
封在本文件里，`price_for()` 返回自有的 `ModelPrice` dataclass，换源不影响调用方。

## 职责边界：只做可观测性

按 [[cost_tracker]] 2026-07-28 那条，免费额度的真钱由 LiteLLM 网关在请求路径上
实时计量，本仓不记第二本账。**这里算错或算不出，误导的是看板，不会算错任何人的
钱。** 这正是失败模式定成"返回 None 并大声说出来"而不是"猜一个像样的数"的原因：
编造的价格看起来是对的，因此比一个显眼的 0 更糟。

## 四条设计规则

1. **绝不抛异常** —— 成本记账是可观测性不是流程控制（`warn_missing_usage` 已有
   的同一条规矩）。litellm 形状变化 / 导入失败 / 条目畸形，一律降级 None。
2. **绝不编价格** —— 上游不认识的 model 就是不认识，`_LOCAL_OVERRIDES`
   **故意留空**，是给运维照着账单填的，不是给我们估的。
3. **每个 model 只警告一次**，不是每次调用一次 —— 2254 次调用会产出 2254 条相同
   警告，那只会训练人去过滤日志。而它取代的 `logger.debug` 是另一个极端：静默了
   几个月没人发现。
4. **惰性 + 记忆化导入** —— `import litellm` 要 1.54s。

## 别名归一化

复用 [[model_catalog]] 的 `resolve_cli_alias`，不另起一张表 —— 那张表已经有
`test_alias_targets_are_registered_catalog_models` 守着，家族出新版本时会一起更新；
自己抄一份就是下一个会腐烂的东西。裸 `haiku` 进账本是**正常输入**不是 bug：
`cli_helper._DEFAULT_CLAUDE_HELPER_MODEL` 就是它，用户也可以自己在 slot 里填
（铁律 #15，平台不干涉用户选模型）。

Tests：`tests/utils/test_model_pricing.py`（解析 / 别名 / 未知不猜 / 警告去重 /
cache 分档计价 / 无 cache 定价时回退到 input 价而非 0 / litellm 失效降级）。
