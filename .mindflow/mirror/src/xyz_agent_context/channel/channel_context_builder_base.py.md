---
code_file: src/xyz_agent_context/channel/channel_context_builder_base.py
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 — `with_current_turn_attachments` 注入当前 turn 附件 marker

**背景**：dev 复盘 agent_93461ec945f5（2026-07-09 16:35）发现，用户在 NarraMessenger 上传的图片虽然被 trigger 正确下载并落盘（audit `attachment_persisted`），但 agent 本轮回复"no image was actually attached"。原因：`ChatModule._synthesize_attachment_markers` 只在 `hook_data_gathering` 遍历**历史**消息（`chat_module.py:508 / :889`）时注入 marker，**当前 turn 没有对应调用点**。当前 turn 的附件要等 `hook_persist_turn` 落到 DB，下一轮才被读取——延迟一轮，用户体感"agent 没看到我的图"。

**修复**：ContextBuilder 基类新增 `with_current_turn_attachments(attachments, *, agent_id, owner_user_id)` 链式方法，把 Attachment 列表 + 定位坐标（agent_id + owner user_id）挂在 builder 上。`build_prompt` 在 `_cap_message_body` 之后遍历这些 Attachment 调 `synthesize_marker(agent_id, user_id)`，把结果拼接到 `info["message_body"]` 尾部——marker 和用户 caption 一起进模板的 `## Current Message` slot。

Marker 格式跟 ChatModule 的历史路径**完全一致**（都来自 `Attachment.synthesize_marker`），agent 侧行为在"当前 turn vs 历史 turn"上无差别。owner_user_id 是 agent OWNER 的 NarraNexus user_id（不是 IM sender_id），因为文件是持久化在 owner workspace 下，`resolve_attachment_path` 用 owner_user_id 才能查到路径。

调用侧：`ChannelTriggerBase._build_and_run_agent` 把 `_resolve_agent_owner` 前置到 `create_context_builder` 之前，然后如果 `attachments` 非空就 `builder.with_current_turn_attachments(...)`；`matrix_trigger._build_and_run_agent_streaming` 做同样处理（重复 super 的模式而不是复用，因为流式路径整段独立）。`backend/routes/websocket.py` 前端 chat 走 raw `input_content` 不用 builder，用一段相同语义的 `Attachment.synthesize_marker` 追加逻辑。Silent batch 路径（group_silent）不需要——silent=True 跳过 step_3，本轮不跑 agent，attachment 已进 `batch_messages[i]["attachments"]` 供下轮记忆。

Regression：`tests/channel/test_current_turn_attachment_marker.py` 5 条覆盖注入/空 list/多 attachment/API 链式/无 attachment。

# channel_context_builder_base.py — 渠道消息 Prompt 组装的抽象基类

## 为什么存在

每个 IM 渠道（Matrix、Slack 等）的消息 prompt 结构是相同的：消息元数据 → 发件人档案 → 历史记录 → 当前消息 → 群成员 → 操作指令。但获取这些数据的方式各渠道不同（Matrix 通过 SDK 查房间，Slack 通过 API 查频道）。

`ChannelContextBuilderBase` 用 Template Method 模式固定组装顺序，只让子类实现数据获取的三个抽象方法，避免每个渠道 Module 重复实现一遍相同的 prompt 拼接逻辑。

## 上下游关系

**被谁继承**：`module/matrix_module/` 里的 `MatrixContextBuilder`（具体名称以代码为准）继承它并实现抽象方法。未来的 Slack Module 也应继承它。

**依赖谁**：`channel_prompts.py` 里的五个模板字符串（`CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 等）；`SocialNetworkRepository`（通过 `get_sender_entity()` 查发件人档案，默认实现返回 None，子类可重写）；`ChannelHistoryConfig` dataclass 控制历史记录行为。

**下游**：`build_prompt()` 的返回值是 **执行 prompt**，作为 AgentRuntime 的 `input_content`。另有 `build_retrieval_anchor()`（2026-06-01 新增）产出**干净检索锚点** `[From <name>] <body>`，由 trigger 放进 `trigger_extra_data["retrieval_anchor"]`，narrative 检索/continuity 只 embed 这个锚点（不再解析执行 prompt）。

## 设计决策

`get_sender_entity()` 在基类里默认返回 `None`——基类不直接依赖 `SocialNetworkRepository`，由子类决定是否查社交图谱。这避免了基类与 SocialNetworkModule 的强绑定（遵循模块独立原则）。

群成员列表（`get_room_members()`）只在成员超过 2 人时才渲染到 prompt 里，1:1 DM 不需要显示成员列表。

`build_retrieval_anchor()`（2026-06-01）用 `get_message_info()` 的结构化字段（`sender_display_name` + `message_body`）直接组锚点，**不解析** execution 模板——因此 build_prompt 的模板格式与 narrative 检索解耦了（旧的 `_extract_core_content` 正则耦合已删除，它在 prod 早已因模板漂移而失效）。

历史记录截断策略是从最旧的消息开始删，最后一条消息（待回复的那条，用 ▶ 标记）永远不被截断。

## Gotcha / 边界情况

`_format_messages()` 的时间戳格式曾被 `continuity._extract_core_content()` 的正则依赖；该函数 2026-06-01 已删除（continuity 改用结构化锚点），所以这层格式耦合不复存在。`_format_messages` 现在只服务于 execution prompt 的历史记录段。

`ChannelHistoryConfig.history_max_chars` 默认 3000 字符，超出后旧消息被截断。截断时会在开头插入 `"  ... (earlier messages truncated)"` 提示，但这个提示本身会占用 chars 计数，极端情况下可能导致即使截断了还是超出，陷入循环——这个 bug 目前未修复。

## 新人易踩的坑

Chat Module 和 Job Module 的 prompt **不经过**这个基类——它们有自己的 prompt 逻辑（文件开头注释里有明确说明）。只有外部 IM 渠道 Module 才用这个基类。别把 ChatModule 的 prompt 构建也改到这里来。
