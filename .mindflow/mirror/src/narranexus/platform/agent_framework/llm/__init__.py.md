---
code_file: src/narranexus/platform/agent_framework/llm/__init__.py
last_verified: 2026-11-25
stub: false
---

## 2026-11-25 — 新增 `decision_model.py`

决策模型(对 state 回答 typed 是/否问题、返回概率)是与 helper LLM 不同的调用
形状,作为本组的一个原子操作单独成文件:厂商中立的客户端 + 每轮凭据解析。


## 2026-09-07 — 公开 `prompt_probe_emit`（批 6c，A2-1）

调用 prompt probe 的两个 helper 后端（anthropic / cli）自批 6b 起在
`builtin.llm_clients` 这个独立 wheel 里，却在 import `llm._prompt_probe`。
公开的是**那一行「记录这条 prompt」的调用**，不是 probe 本身——
probe 自己拥有文件 sink 和采样策略，仍然私有。
名字带上子系统前缀（`prompt_probe_emit` 而非 `emit`），因为在包级别 `emit` 说不清是谁的。
惰性 re-export：为了 `helper_sdk` 而 import 本包的人不该顺带把 probe 的 sink 造出来。

# agent_framework/llm/__init__.py — group anchor

Created in the 2026-07-24 agent_framework regrouping (61 flat files →
loop/ adapters/ llm/ providers/ + 2 cross-cutting root files). Atomic LLM operations — single calls, no agent loop: the
protocol-keyed helper factory (helper_sdk) with its anthropic/cli/gemini
backends, failure classification (failure), and audio transcription
(transcription/). (llm_api's empty leftover shell was deleted here —
embedding moved out long ago; zero imports remained.)
