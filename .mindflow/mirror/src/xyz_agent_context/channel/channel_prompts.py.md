---
code_file: src/xyz_agent_context/channel/channel_prompts.py
last_verified: 2026-08-06
stub: false
---

## 2026-08-06 — voice register 实测硬化（dev 网关真 V4 Flash bench）

裸 register 首版实测两个失败模式：工具后答案漏成 prose（只有进度没结果）、长答案完全绕开 speak。两步硬化后 5 场景 × 多样本全过：①「plain text = 私人笔记，用户永远听不见」的重构式表述（比禁令服从率高）；②「工具完成后必须再 call speak 交付答案」「长答案拆成连续多个短 speak」写成显式规则。实测（经 dev litellm 网关）：TTFT 0.8–1.9s、首段 speak 参数 1.2–3.4s、markdown/URL 零泄漏、预告纪律 100%、长答案 4–6 段口语分段（3/3 一致）。尾部 prose 泄漏在 NexusPower 里是 monologue（桥只消费 speak deltas），用户不可见。reasoning_effort 档位结论待网关侧透传确认后回写（跟踪在 reference/self_notebook/todo/）。

## 2026-08-06 — voice fast mode: RTC 检测 + voice register + speak

新增 VOICE_REPLY_INSTRUCTION_TEMPLATE（channel 无关的 voice register，handoff §7 行为纪律），任何 channel 检测到语音 turn 即可复用。

## 2026-08-06 — 通讯协议按会话类型分叉（1:1 私聊不再吃群聊纪律）

原来 `CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 把**一份**通讯协议注入所有渠道轮次，
`room_type` 只是「Conversation Type」那行的展示值，协议段落根本不读它。于是
1:1 私聊也收到 `Your default action is NO REPLY.` / `When to Reply (rare)` /
群聊纪律 / @mention 纪律。真人私聊发 "hello"，按这套规则**沉默才是正解**——这就是
0802 微信工单。微信是极端案例：它的 builder 把 `room_type` 硬编码成
`ROOM_TYPE_DIRECT`（个人号 v1 只有私聊），所以**每一轮**都命中。

关键论据在下面「设计决策」那段本来就写着：这套协议 2026-03 是为**三个群聊问题**
调优的（agent 间确认循环、群消息唤醒所有成员、@mention 滥用）。套到真人 1:1 上
从来不是设计意图，是连带伤害。

现在的结构：

- `ROOM_TYPE_DIRECT` / `ROOM_TYPE_GROUP` 常量。六个 builder 原先各自手写这两个字面量
  ——那时它只是标签，写错也只是显示问题；**现在它选协议，是真契约**，所以收进常量。
- `COMMUNICATION_PROTOCOL_GROUP` = 原文原样搬出，一字未改。
- `COMMUNICATION_PROTOCOL_DIRECT` = 新写。`Replying is the default.`；沉默口子收窄到
  「对方那条是纯确认（好的/谢谢/收到/👍）且你无新内容可加」；显式列出必须回的情况
  （打招呼、答不全的问题、做不到的请求、闲聊）；**风格规则原样保留**（简洁、一条消息
  一个目的、不表演式汇报）——那些从来和房间人数无关；另加「不许承诺未来的活」，与
  `step_3` 兜底文案 2026-07-30 那两条诚实性规则同源。
- `communication_protocol_for(room_type)`：**只有精确等于 `ROOM_TYPE_DIRECT` 才给私聊版**，
  其余（含 None、大小写不符、未知类型）一律群聊版。不对称是刻意的：在没认出来的房间
  类型里过于安静是可恢复的，往 500 人群里乱发不是。
- 模板里协议段落换成 `{communication_protocol}` 占位符，由
  `ChannelContextBuilderBase.build_prompt()` 按 `room_type` 填。**这是个必填占位符**
  ——手工渲染模板的测试夹具（`test_channel_prompts_path_rules.py`）也得跟着填，
  否则 `.format()` 抛 `KeyError`。

连带修正：Slack 的 builder 原先把 room_type 硬编码 `Group Room`，理由是「DM 和频道
接口一样，简化 prompt 且不损失保真度」——这个理由在 room_type 变成行为开关后失效，
Slack 私聊会继续吃群聊纪律。已改成按 `D...` 频道 id 前缀判定。

测试：`tests/channel/test_dm_communication_protocol.py`（两份协议的内容边界、选择
函数的保守回退、模板占位符接线）。

# channel_prompts.py — 所有 IM 渠道共用的 Prompt 模板库

## 为什么存在

渠道消息 prompt 的结构性文字（"你收到了一条来自 X 的消息"、"发件人档案"、"历史记录"等段落头）在所有渠道间是一样的，变化的只是填入的数据（渠道名、消息体等）。集中管理这些模板有两个好处：调整措辞时一处修改全渠道生效；方便审查和迭代 prompt 效果。

`CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 是最关键的——它定义了整个渠道消息的框架。"通讯协议"章节（规定 Agent 何时回复、何时沉默）自 2026-08-06 起**不在模板里**，而是 `COMMUNICATION_PROTOCOL_GROUP` / `COMMUNICATION_PROTOCOL_DIRECT` 两份，由 `communication_protocol_for(room_type)` 选一份填进 `{communication_protocol}` 占位符。群聊那份是防止 Agent 陷入"自说自话"死循环的核心护栏；私聊那份反过来——防止 Agent 对真人的直接提问装死。

## 上下游关系

**被谁用**：`ChannelContextBuilderBase.build_prompt()` 用 `.format(**info, ...)` 填充 `CHANNEL_MESSAGE_EXECUTION_TEMPLATE`；`_build_sender_profile()` 用 `SENDER_PROFILE_FROM_ENTITY_TEMPLATE` 或 `SENDER_PROFILE_UNKNOWN_TEMPLATE`；`_build_history_section()` 用 `CONVERSATION_HISTORY_TEMPLATE`；`_build_members_section()` 用 `ROOM_MEMBERS_TEMPLATE`。

**无其他依赖**：这个文件只有字符串常量，不导入任何其他模块。

**~~隐式消费者~~（这条已过期，2026-08-06 更正）**：曾经 `narrative/_narrative_impl/continuity.py` 的 `_extract_core_content()` 依赖模板输出以 `[Matrix · ...]` 开头。该函数 **2026-06-01 已删除**——continuity / 叙事检索改用 `build_retrieval_anchor()` 产出的结构化锚点，与执行模板解耦（见 `channel_context_builder_base.py.md` 同日条目）。改模板格式不再有这层耦合风险。

## 设计决策

"通讯协议"章节（"## Communication Protocol"）是 2026-03 经历多轮调优后写入的规则集，解决了三个核心问题：
1. Agent 之间的对话容易陷入"收到→好的→明白了→好的"的无效确认循环
2. 群聊里每条消息都会触发所有成员的 AgentRuntime，但大多数消息不需要每个人回复
3. @mention 被滥用导致每个人都被强制处理不相关消息

这些规则是通用的，不应写入具体 Agent 的 Awareness——Awareness 处理的是"这个 Agent 是做什么的"，通讯纪律是基础设施层面的规范。

## Gotcha / 边界情况

`CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 里的 `{channel_key}` 占位符出现在 Instructions 第 5 条里（`contact_info.channels.{channel_key}`），这是 `get_message_info()` 返回的字段之一。如果子类的 `get_message_info()` 没有返回 `channel_key`，`.format()` 会抛 `KeyError`。

模板里有中英文混合的示例（"好的"、"谢谢"等），这是刻意的——系统主要面向中文用户，给 LLM 提供中文表达的反例让它更好地识别无效确认语。

## 新人易踩的坑

模板里有两个"消息目标"的说明：`matrix_send_message` 回复渠道房间，`send_message_to_user_directly` 发送给 owner。这两个工具名是硬编码在模板里的。如果渠道的 MCP 工具名改了，必须同步更新这里的说明，否则 Agent 会用错工具。

**File & Path Rules for IM Delivery（Bug 23，2026-04-20 加）**：模板里有一节专门告诉 agent——**IM 对端读不了本地路径**。场景就是 agent 干完活把内容保存成了文件，然后直接回复"保存在 /app/xxx.md 了"。IM 用户看到一条他永远打不开的路径。解法三选一：短内容内联进消息、中长内容创建 Lark 文档发 URL、二进制文件走 Lark 文件上传 API。这和 `basic_info_module/prompts.py` 的 deployment_context 是联动的——后者在 **system prompt** 层提醒 agent "你在容器/本地机里，用户能不能触到你的路径"；这里在 **每条 IM 消息的 runtime prompt** 层重复强调（防止 agent 在长 context 里忘了）。**修改时保持 3 条 delivery route 的结构**，有测试（`tests/channel/test_channel_prompts_path_rules.py`）pin 住。
