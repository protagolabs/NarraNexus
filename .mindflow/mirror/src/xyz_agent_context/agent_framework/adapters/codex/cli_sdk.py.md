---
code_file: src/xyz_agent_context/agent_framework/adapters/codex/cli_sdk.py
stub: false
last_verified: 2026-07-27
---


## 2026-07-27 — driver 表面一致化：capabilities() 空协商缝 + 签名整形

三个 driver（claude / codex v1+v2 / remote）统一新增 `capabilities() ->
set[str]`（全部返回空集 = 今天的行为；词汇表见 driver.py 注释，只在能力
真正实现的同一变更里声明）。`streaming` 全员改 keyword-only（所有调用点
本就关键字传参，零行为变化）。codex v2 的 `del kwargs` 改为显式 WARNING
（此前 `disallowed_tools` 被静默丢弃——调用方以为约束生效了）。契约测试
`tests/agent_framework/test_driver_contract.py` 钉住整个表面。

## 2026-07-27 — 事件类型字面量收敛到 loop/events.py 常量

六种事件形状的字符串字面量改为 import `loop/events.py` 的常量
（TYPE_RAW_RESPONSE_EVENT 等），值逐字节不变——纯机械替换，行为零变化。
事件契约自此有唯一事实源，详见 events.py.md。

