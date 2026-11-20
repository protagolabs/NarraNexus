---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/_rtc_voice.py
stub: false
last_verified: 2026-11-19
---

## Why it exists

F28 语音快速回答模式（voice fast mode）的入站契约层：Hybrid 把 final STT
以人类 Matrix 身份发成普通 `m.text`，同一事件附带
`ai.netmind.rtc.voice_input` v1 metadata。本文件是该契约在 Nexus 侧的
**唯一**解析与校验点——trigger（`matrix_trigger._wrap_event` /
`parse_event`）只调用这里的纯函数，不自己碰 metadata 字段。

契约源：Hybrid「Direct Matrix RTC 快速回覆」handoff（Lark wiki
`Gr9Qwkes2iCF2VkrbmslBpQrgih`）§3.1/§3.2/§3.4/§4.2；方案全貌见 PRD
`WTHSdPt4topN7YxnXPcl4wV3gRd` §4.3。

## Design decisions

- **两级触发契约。** 严格解析（`parse_rtc_voice_input`）成功 →
  完整 voice turn，带四个 correlation ID（`rtc_session_id`/`turn_id`/
  `invocation_id`/`agent_profile_id`）。严格解析失败但 metadata 里仍带
  非空白 `voice_instructions` 字符串（`extract_common_voice_instructions`，
  即"common trigger"）→ 降级 voice turn：有语音模式、无 correlation。
  两者都没有 → 按普通文字消息处理。三种情况下都**绝不中断正常回复路径**。
- **严格校验、宽松失败。**§3.2 的每条规则都是 `===` 语义：`version`/`seq`
  用 `type(x) is int` 排除 bool（Python 里 `True == 1`，直接 `==` 会把
  `version: true` 放进来——变异测试钉住了这一点）；`transcript_final`
  必须 `is True`。任何一条不满足返回 `None`，上游把事件当普通文字消息，
  **绝不因坏 metadata 中断正常回复**。
- **voice_instructions 是唯一宽容字段**：缺失/空白/类型错 → 降级为
  `None`，voice turn 本身仍然合法（契约明文要求）。
- **common trigger 只认 metadata，不碰 body。**
  `extract_common_voice_instructions` 复用与 `parse_rtc_voice_input` 相同
  的事件壳校验（`m.room.message` + `m.text` + metadata 是 dict），但跳过
  §3.2 的四个 ID / version / seq 强字段——它是严格解析失败之后的兜底
  通道，故意不去解析 body envelope，保证模式判定不依赖 body 字符串解析。
- **`transcript_final is False` 是 common trigger 的唯一 carve-out
  （2026-08-11 review）。** 后端显式标 "not final" 是 turn 边界信号，
  不是坏数据：interim STT 片段绝不能进降级 voice 模式（会被当成完整
  utterance 念出来）。判定是 **identity**（`is False`）——缺失/类型错
  （含 `0`、`"false"`）仍算坏数据、照走 common trigger。
- **降级路径的 instructions 有 2000 字符上限**
  （`COMMON_INSTRUCTIONS_MAX_CHARS`）：common trigger 放宽了构造门槛，
  无界字符串会白白撑大 prompt；严格路径由契约本身约束、不截。
- **metadata 不是授权凭证**：本层只做识别，不做任何权限判断；调用方必须
  保留既有 sender/room 校验（authorize-event 门禁照过）。
- **envelope 只认开头**：`split_narra_system_prompt` 仅剥离 body 起始处的
  `<narra-system-prompt>` envelope；未闭合、出现在正文中间的一律不剥，
  整个 body 按 transcript 处理——用户原文永远不能被提升为指令内容。

## 2026-11-19 — 出站绑定：`ai.netmind.rtc.voice_reply`（语音契约 v15）

本文件原先只管入站；v15 起也是**出站回复绑定**的唯一定义点：

- `RTC_VOICE_REPLY_KEY` 字面量。
- `voice_reply_binding(rtc_voice)`：从 `raw["rtc_voice"]` 取 `{rtc_session_id, turn_id}`，两个 id 都是非空字符串
  才返回，否则 None。降级轮天然没有 id，所以无法绑定——这是有意的，不拿别的轮的 id 去凑。
- `with_voice_reply_binding(content, binding)`：返回盖好章的**新** dict（不改入参）；content 带
  `m.new_content` 时两层都盖。`binding=None` 原样返回，保证文字轮事件逐字节不变。

- `is_leaf_tool(tool_name, leaf)`：「工具名恰好是 `leaf`，裸名或 `...__leaf`」这条规则的**唯一定义点**。
  trigger 用它决定「这段 `speak` / `narra_reply` 文本发不发进房间」，reply extractor 用它决定「这一轮算不算已回复」。
  两边必须是同一个集合：trigger 发了而 extractor 不认 → DM 兜底再补一条（v15 下那条带着本轮绑定，可能顶掉真回答）；
  extractor 认了而 trigger 没发 → 静默轮。此前这条规则在两个文件里字面复制了四份，没有任何机制保证一致。
  放在本文件是因为它零重依赖：extractor 是 descriptor 里的 REF，不能为了一个判定去 import 带着 nio + SDK 的 trigger。
  `tests/.../test_voice_reply_binding.py::test_trigger_delivery_and_reply_extractor_agree_on_reply_tools` 钉住两侧一致。
- `with_voice_reply_binding` 对**不是 dict** 的 binding 也原样返回：binding 要穿过 turn envelope（可能被序列化），
  坏值不该让兜底发送抛异常。

- `MAX_RTC_ID_LEN`（128）+ `is_rtc_id`：语音契约里所有关联 id 的长度上限，入站解析（`parse_rtc_voice_input`）、
  出站绑定（`voice_reply_binding`）和控制事件解析（[[_voice_call.py]]）共用。超长的严格解析失败，语音轮退成
  「降级语音轮」（有语音模式、没有关联 id），照常被回答。为什么要有上限、为什么是拒绝而不是截断，见 `_voice_call.py.md`。

契约源：Narra「升級到第 15 版語音通話契約」文档 §C8（2026-11-19，Poliyka 提供）。所有出口的清单见
[[matrix_trigger.py]] 同日条目。

## Downstream

出站绑定的消费方：[[matrix_trigger.py]]（bridge / plain sender / error marker / 熔断拒绝）、
[[_voice_control.py]]（`_claim_voice_turn` / `_drop_dead_voice_turns` 用 `voice_reply_binding` 判断这一轮有没有关联 id）、
[[narramessenger_context_builder.py]]（`reply_kwargs`）、[[narramessenger_module.py]]（`send_to_agent`）。

入站：被 trigger 的 voice 检测分支消费（fast profile 触发 + envelope 注入；
通话级串行自 2026-08-11 起按 agent:room key，不再取 rtc_session_id）。`parse_rtc_voice_input` 与
`extract_common_voice_instructions` 自 2026-08-11 起是
`matrix_trigger._detect_voice_turn` 两级检测的 Level 1 / Level 2 判据；
两者共用私有 `_voice_metadata` 事件壳守卫（m.room.message + m.text +
metadata 是 dict），壳判定永不分叉。测试：
`tests/narramessenger_module/test_rtc_voice_input.py`（合法/全部非法变体/
envelope 边界，含 bool-as-int 变异防护；common trigger 的独立用例组
`TestExtractCommonVoiceInstructions`；`RTC_VOICE_INPUT_KEY` 字面量钉子）。
