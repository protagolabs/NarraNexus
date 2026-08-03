---
code_file: src/xyz_agent_context/module/managed_channel_ingress.py
last_verified: 2026-08-03
stub: false
---

# managed_channel_ingress.py — 托管模式的 trigger 执行体宿主

## 为什么存在

Manyfold 托管模式下平台持有连接与清洗流水线,IM 消息以 chat turn 形式
经 `/v1/chat/completions` 转发进来;但原生 channel trigger 的
per-message **业务钩子**(wechat 首聊认主、narramessenger authorize
门、inbox 写入、错误兜底直发)都长在平台接管走的那条收消息路径上,
prompt 级映射够不着。本文件是这些钩子在托管模式下的宿主:按
CHANNEL_TRIGGER_MAP 惰性构造 trigger 实例(**绝不调 start()**——无订阅
循环、无连接),在 openai_compat 的 run 前后路由
`managed_before_run` / `managed_after_run`。

## 上下游

上游:`backend/routes/openai_compat.py`(gate 在 BackgroundRun 构造前,
deny 直接回执;after_run 在流收尾 finally 里 fire-and-forget + done
callback)。下游:`channel_trigger_base` 的 managed 缝(默认实现 +
wechat/matrix 覆写)。它是 `run_channel_triggers` 的对等物(trigger
注册表上的协调器),不是 Module——铁律 #3 不受影响。

## 关键决策 / Gotcha

- **失败语义按钩子性质分叉**:副作用渠道 fail-open(构造失败/钩子异常
  → 放行,下游自然暴露 no_credential),narramessenger fail-closed
  (它的钩子就是授权门;类缺失/异常 → deny)。
- `synthesize_managed_message` 只重建业务钩子需要的最小 ParsedMessage;
  wechat 回复路由读 `raw["context_token"]`(wire 上是 reply_token)。
- 单例 + per-channel 实例缓存;构造失败按渠道隔离(与
  run_channel_triggers 同款防御姿态)。
