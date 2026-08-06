---
code_file: tests/agent_framework/test_helper_latency_swap.py
last_verified: 2026-08-06
stub: false
---

# test_helper_latency_swap.py — fast-model swap 的三重门与接口契约

## 为什么存在

守住 `_latency_swap_model`（[[openai_agents]] 2026-08-06 条目）的边界：swap 只在
「显式 opt-in × 允许 host × 已知思考型模型」三个条件同时成立时发生。三个门各自
对应一条会写坏用户体验的反例——未 opt-in 的对话轮被换模型（铁律 #15）、OpenRouter
上换成不存在的 id（404）、用户主动选的快模型被覆盖。env 三旋钮
（`HELPER_FAST_MODEL` / `..._HOSTS` / `HELPER_REASONING_MODEL_PREFIXES`）逐个有
开与关的用例。

另外两条是**跨文件契约测试**：三个 helper SDK 的 `llm_function` 都必须收
`latency_sensitive`（调用方不知道解析到哪个协议）；narrative 前置三调用点
（continuity / unified judge / single confirm）必须 opt-in——防止将来重构悄悄
把热路径挪回思考型模型。用 `inspect.getsource` 断言而不是跑真调用，是因为这里
守的是"调用点声明了什么"，不是"LLM 返回了什么"。
