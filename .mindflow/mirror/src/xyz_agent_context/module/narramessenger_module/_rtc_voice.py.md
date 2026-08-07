---
code_file: src/xyz_agent_context/module/narramessenger_module/_rtc_voice.py
stub: false
last_verified: 2026-08-06
---

## Why it exists

F28 语音快速回答模式（voice fast mode）的入站契约层：Hybrid 把 final STT
以人类 Matrix 身份发成普通 `m.text`，同一事件附带
`ai.netmind.rtc.voice_input` v1 metadata。本文件是该契约在 Nexus 侧的
**唯一**解析与校验点——trigger（`matrix_trigger._wrap_event` /
`parse_event`）只调用这里的纯函数，不自己碰 metadata 字段。

契约源：Hybrid「Direct Matrix RTC 快速回覆」handoff（Lark wiki
`Gr9Qwkes2iCF2VkrbmslBpQrgih`）§3.1/§3.2/§4.2；方案全貌见 PRD
`WTHSdPt4topN7YxnXPcl4wV3gRd` §4.3。

## Design decisions

- **严格校验、宽松失败。**§3.2 的每条规则都是 `===` 语义：`version`/`seq`
  用 `type(x) is int` 排除 bool（Python 里 `True == 1`，直接 `==` 会把
  `version: true` 放进来——变异测试钉住了这一点）；`transcript_final`
  必须 `is True`。任何一条不满足返回 `None`，上游把事件当普通文字消息，
  **绝不因坏 metadata 中断正常回复**。
- **voice_instructions 是唯一宽容字段**：缺失/空白/类型错 → 降级为
  `None`，voice turn 本身仍然合法（契约明文要求）。
- **metadata 不是授权凭证**：本层只做识别，不做任何权限判断；调用方必须
  保留既有 sender/room 校验（authorize-event 门禁照过）。
- **envelope 只认开头**：`split_narra_system_prompt` 仅剥离 body 起始处的
  `<narra-system-prompt>` envelope；未闭合、出现在正文中间的一律不剥，
  整个 body 按 transcript 处理——用户原文永远不能被提升为指令内容。

## Downstream

被 trigger 的 voice 检测分支消费（fast profile 触发 + envelope 注入 +
rtc_session_id 通话级串行的 key 来源）。测试：
`tests/narramessenger_module/test_rtc_voice_input.py`（合法/全部非法变体/
envelope 边界，含 bool-as-int 变异防护）。
