---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/prompts/library.py
last_verified: 2026-08-24
stub: false
---

## 2026-08-24（补)— wait_timed_out 超时提示

`wait_timed_out(seconds)`:`wait_for_input` 等满 N 秒无消息时,loop WAIT 边界注入的提示。告诉 agent 等待已结束、收尾即可(别以为还在等),并**点名**可"再等一次"的替代但不推它——让真闲的轮收口而非空转。措辞与工具 description 对齐(只在真有回复将至时再等)。

## 2026-08-13（管线审后）— reminder 撤回「ONE call」与 VOICE 多段契约的冲突

「Call ONE reply tool with your complete answer」与 VOICE register 的「long answers
become SEVERAL short speak calls / 预告→答案两连调」直接打架（管线审 I#2）。改为
「连续 reply 调用续写长答案、工具前的进度短句合法；但绝不重复已交付内容」——反重复
半句保留（它是桥等值去重的提示侧对位）。

## 2026-08-13 — reply_reminder 声明默认回复工具（不再平铺列表）

ExpressionContract 的声明序契约（首位=本轮默认回复工具）此前只活在 constitution 的
example slot；动态尾 reminder 把列表平铺，8/13 语音通话实测模型 12/14 轮跟随平铺列表
选了 narra_reply 而非按 per-message 指令用 speak。reminder 现渲染
「THIS TURN's default reply tool is X (other reply tools, only when …)」+
「Call ONE reply tool …never repeat」；消息自带 reply instruction 仍然最高优先。
模板占位从 {{REPLY_TOOLS}} 换为 {{DEFAULT_REPLY_TOOL}}/{{OTHER_REPLY_TOOLS_CLAUSE}}。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

constitution 例子去静态化:`constitution.md` 里的 `send_message_to_user_directly`
硬编码删除,换 `{{DEFAULT_REPLY_TOOL_EXAMPLE}}` 槽位,由 inputs.default_reply_tool
填充(框架 copy 永不写平台工具名;mute 回合无例子可给)。新增
`reply_reminder(reply_tools)`(资源 `reply_reminder.md`):assembly 每步从
ExpressionContract 现值渲染进动态尾部,措辞含「消息自带回复指令优先于默认名单」
——channel 模板(平台侧)与 harness 不再对打。旧 reminder 是 assembly 里的
硬编码英文串,已迁来资源(copy-in-resources 归位)。

## 2026-07-29 — 两份中文占位稿删除，意图收进本文

`resources/compaction.md` 与 `resources/prescriptions/README.md` 是中文设计稿
占位，`library.py` 从未装载过它们——既碰铁律 #22（仓库不留开发记录），日后
真做出来还得是英文 prompt（#1）。占位文件删掉，**设计意图记在这里**（mirror
才是意图的载体，md 占位不是）：

**压缩提示词（做的时候按这个来）**：摘要保留任务状态 / 未完成事项 / 关键文件
与决定，丢过程性噪音；已有 summary 时**增量更新**而不是推倒重来；产出用
"REFERENCE ONLY" 前缀 + 结束 marker 包裹并声明「不要回答摘要中提到的问题」
（防注入）；压缩前先提醒 agent 把重要信息写进长期记忆再压。

**模型行为处方（S2）**：按模型族一个 md（`anthropic.md` thinking 块惯例、
`openai.md` 强制工具使用特化、`weak_models.md` 显式工具调用引导），装配时由
`model_prescription_section` 按 `ModelParams.model` 匹配加载，无匹配返回空串。
铁律 #15 划的线：处方是**通用行为引导**，不是对用户所选模型的限制。

# prompts/library — NexusPowerPrompts 命名空间类

Owner 拍板形态:不可实例化(构造抛 TypeError,无状态是强制)、classmethod 取串、子类覆写=实验包。宪法任何 mode 非空(框架最低身份)。section_order 即契约:改顺序=打穿全网 cache 前缀,必须显式评审。资源经 importlib.resources+lru_cache 装载,缺模板=构建错误不兜底。

## 2026-08-18 — 工具改名映射（新增条目；上面带日期的历史条目一律不改写）

本文件上方带日期的条目里出现的是**当时**的工具名，故意保持原样 —— 镜像的价值就在于它记的是
那一天发生了什么，在带日期的条目里改名会让「什么时候变的、从什么变的」不可考。第三轮预审在
23 个文件里查出 68 处这种改写，已全部还原。

现行名字与旧名字的对应：

| 旧 | 新 |
|---|---|
| `send_message_to_user_directly` | `reply_owner`（回答刚说话的 owner）/ `notify_owner`（未被问就主动告知） |
| `bus_send_message` | `message_team` |
| `bus_send_to_agent` | `message_agent` |
| `bus_get_messages` | `read_history`（且改为按会话把手取，不再收 channel_id） |
| `bus_create_channel` | `create_team` |
| `bus_share_to_team` | `team_share_file` |
| `work_add_item` / `work_complete_item` / `work_update_status` … | `team_work_add` / `team_work_complete` / `team_work_update_status` … |
| `ChannelInboxWriter` | `InboxRecorder`（且改写自己的两张表，不再写 bus 表） |

规范解释见 [[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。
