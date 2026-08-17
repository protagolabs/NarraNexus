---
code_file: src/xyz_agent_context/module/message_bus_module/message_bus_module.py
last_verified: 2026-08-16
stub: false
---
## 2026-08-16 — 静态段里最响的那句，正是 team 房间逐字反驳的那句

2026-08-12 那轮把「只看得到 @ 你的消息」和「未回复会重现」改成了处处成立的说法，
**但漏掉了同一段里语气最重的一条**——而它恰恰是矛盾最尖锐的一条：

> "Finished work is never ping-pong... send the result via `bus_send_message`。
> **纯文本收尾 = 零交付，对方永远看不到**"

在 team 房间里这句正好相反：纯文本**就是**回复（[[message_bus_trigger]] 的
`_deliver_reply` 替它上墙），而 turn prompt 明写「**禁止**用 bus_send_message 投递
本条回复，否则双发」。两句话同一个上下文窗口，讲同一件事，结论相反；而静态段那句
因为背着 8/1 briefing squad 的 P0，语气被反复加重，**是更容易赢的那一句**。

修法沿用 8-12 的既定套路（不分叉、说处处成立的话）：**保留义务，交出机制**。
「干完的活必须 REACH 对方」是普适的，怎么送由这一轮的 surface 决定——给了投递工具
就用工具，turn prompt 说了替你上墙就写纯文本。两条路都点名，因为两条路都真实存在。

同批扫掉的另外两处（铁律 #8——上一条 mirror 记的教训就是"修一份留一份"）：

- **「Just stop the turn」**：那是一条关于**工具调用**的规则，只在"不调工具就等于
  没输出"的 surface 上等于沉默。会自动上墙的 surface 上，残留的纯文本仍是一条消息。
  改成「不调工具**且**不留任何回复文本」，并说明沉默是"什么都不产出"而非"产出得短"。
  顺手删掉尾巴「unread cursor advances appropriately」——同一个游标下面两行的
  resurfacing 规则已经讲得又准又有作用域，这是第三份含混说法。
- **`[MessageBus · …]` = "另一个 agent，不是你的 owner"**：这个标签标的是**路由**，
  不是发送者的物种。team 房间里用户自己以 `usr_<id>` 发言，会同时进未读列表和 input
  源标签——于是**老板本人说的话被声明为"这是台机器"**，还紧挨着一条"跳过寒暄"。
  改成「usually another agent，但**人也会在 bus 上说话**，读标签里的发送者，别默认是机器」。

配套 `_render_sender()`：`usr_*` 在未读列表和 input 源标签里都渲染成 `User`。
「读发送者」这条指令，只有发送者可读时才成立，而 `usr_a1b2c3` 对模型什么都不说。
命名与 [[message_bus_trigger]] 的 `_sender` 一致——同一行数据，两处露出，一个叫法。

`USER_SENDER_PREFIX` 从 `schema.team_schema` import（不是重打字面量）：该前缀全仓
唯一定义点的约定见 [[team_schema]] 2026-08-11 那两条。

测试：新增 6 条进 `tests/message_bus/test_visibility_wording.py`（该文件的立意就是
"静态段的每句话必须在它到达的每个房间里成立"）。
`tests/module/test_a2a_ask_another_agent_guidance.py` 里那条 `assert "plain text"`
**锁的是机制**，随之改为锁义务（`has to REACH them`）——它写的时候机制还是普适的，
team 房间出现后就不是了。义务本身仍然被钉住，P0 没有松。

**未修、留作独立改动的**（都不是文案层）：① team 轮次没有屏蔽 bus 投递工具
（`get_disallowed_tools` 这个钩子存在但本模块没实现），文案劝阻 ≠ 工具消失；
② 级联上限 `MAX_TEAM_AGENT_HOPS` 只实现在 `_deliver_reply` 一条路上，
`bus_send_message` 直接写 team 房间不受任何计数约束；③ `get_unread` 不排除当前
team 房间，房间消息在 scrollback 之外被二次渲染。

## 2026-08-10 — 这台 MCP server 上挂了第二套工具族

`create_mcp_server` 除 `_message_bus_mcp_tools` 外,还注册
[[_work_board_mcp_tools]](5 个工具:add / list / claim / complete /
update_status)。

**挂同一台 server**:工作项的作用域是 team **房间**,而房间就是一个 bus
channel —— 能在房间里说话的 agent,恰好就是该维护这块板子的 agent。独立 Module
要新端口、新 instance 生命周期,还得反过来查 bus 的表(铁律 #3)。

但它有**自己的状态机和自己的写入边界**(`stalled`/`paused`/`cancelled` 模型不
可写),所以分文件。只读本文件会以为这台 server 上只有消息工具。

## 2026-08-05 — 指令不再把模型送去调一个会破坏名录的工具（review）

「When NOT to Call Tools」里那句 `Do NOT call bus_register_agent unless your
profile needs updating` 有两重问题：工具本身已删（见
[[_message_bus_mcp_tools]] 同日条），而且原文恰恰在**「想更新 profile 时」**
把模型指向它——那正是它会把 `owner_user_id` 写空、让自己从同 owner 搜索里消失
的路径。改写成「平台没有注册工具；要改同伴看到的内容用
`update_agent_profile`（Awareness），capabilities 是推导的、不能自报」。
有测试断言指令里不再出现旧工具名、且出现新工具名。

## 2026-08-04 — 名录写入交给统一 seam；Known Agents 不再打印占位符

两处（P1 段02）：

1. `hook_data_gathering` 里那段内联注册（硬编码 `capabilities=[]`、把
   `agent_description` 原样当描述发布）换成调 [[agent_discovery_sync]] 的
   `sync_agent_discovery`。那段代码是"`bus_search_agents` 对任何查询都返回空"
   和"配置好的 agent 被报成待配置"的直接原因（prod 全表 488 行）。现在这里只是
   **每轮的幂等兜底**——真正的注册发生在创建/配置那一刻（[[auth]]、
   [[awareness_module]]、[[install_pipeline]]）。
2. `_volatile_context_parts` 的 Known Agents 渲染：描述判定为 unset
   （[[entity_schema]] 的 `is_agent_description_unset`）时**整段不渲染**，而不是
   把占位符打出来。每一行都写着同一句"待配置的新 agent"，等于告诉发问的模型
   「这些同伴都不可用」——owner 说"问问教学专家"时它无从下手。

## 2026-08-04 — bus 轮次的回复面声明（origin-aware）+「干完活必须交付」纪律

P0 recvrdLPavENwg（8/1 briefing squad：5 个分析师真研究、纯文本收尾、零交付）
的声明侧修复。新增 `get_expressive_tools(ctx_data)` 覆写：**只在**
working_source=MESSAGE_BUS 的轮次声明 `bus_send_message` + `bus_send_to_agent`
（fully-qualified，派生自 get_mcp_config().server_name）。三重门：
① 非 bus 轮不声明（chat 轮广告 bus 工具会诱导经 bus 回 owner）；
② team 房（extra_data `bus_team_room`，由 [[message_bus_trigger]] 盖章）不声明——
纯文本自动上墙、prompt 禁投递工具，声明会诱导双发；③ 无 ctx 不声明。
配套 `owns_working_source`：收集点（[[context_runtime]]）把来源模块的声明排
到最前，默认回复工具从此跟着「谁联系的你」走。

Reply Discipline 同批加一条「**Finished work is never ping-pong — deliver it**」：
沉默许可只给"没实质内容"，做完别人求的活必须用 bus 工具送达，纯文本收尾
= 零交付。注意：与 2026-08-01 那条同理，文案对弱模型效力有限，真正的机制
修复是声明面（本条）+ 判定面（message_bus/__init__）对齐。

## 2026-08-01 — 指令新增「替 owner 去问另一个 agent」剧本

P1 段 06:owner 说"问问教学专家在干嘛",agent 答做不了。能力一直都有
(`bus_send_to_agent` 会触发对方),缺的是**把这类请求认出来并给出路线**。
新增小节明确:① 这类请求你能做,**不得回答无法联系其他 agent**;
② 从 Known Agents 取准确 id;③ 用 `bus_send_to_agent` 发问,
**别用社交网络/联系方式工具**(那返回联系方式,不是答案);
④ 告诉 owner 已问、回复会另开一轮;⑤ 对方回复到达时用
`send_message_to_user_directly` **回报给 owner**——并写明
Reply Discipline 只管对**同伴**的回复,绝不压制对 owner 的回报
(不写这句,那条"没实质就沉默"的规则会把用户要的答案吞掉)。
找不到目标要问清楚,那是澄清问题、不是拒绝。

文案进 `_static_instruction_parts`(静态、逐字稳定,可缓存),有测试断言
稳定性与各条要点。

Reply Discipline 同批加了一条「问题从来不是 ping-pong,必须回答」——含
「替 owner 转达的问题」和「回报自己 owner 不算交差」。**但要知道:光加这
条文案对被测模型无效**(真机 3/3 仍拒答),真正起作用的是
[[message_bus_trigger]] 那侧把假的 Owner Relay 指令换掉。这条文案保留是
因为它本身正确、且对强模型有用,**不要**把它当成该问题的修复。

## 2026-07-28 — R4b：三个数据列表搬进 get_turn_context

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

`get_instructions` 原本 = 使用规则 + Known Agents / Your Channels / Unread
Messages 三个列表；unread 每轮消费必变、另两个被 bus 工具会话中途改变
（prod 稳定性 11/17）。现拆为：

- `_static_instruction_parts()` — 使用规则（仅烘焙 self.agent_id，会话内恒定）。
- `_volatile_context_parts(ctx_data)` — 三个列表，渲染逻辑（MAX_* 上限、顺序、
  文案）零改动。
- `get_instructions` — flag 开 → 只拼 static（轮间字节稳定）；关 → static +
  volatile 同块拼接（legacy 逐字节一致）。
- `get_turn_context` — `### MessageBus — Current State` 稳定标题 + 三个列表；
  三个列表全空 → ""。

"unread messages are already injected into your context automatically"（规则
段 :182 附近）的表述依然成立——注入位置变了，行为没变。

第 4 步 "Fetch channels" 的查询原本 `ORDER BY c.updated_at DESC`，但
`bus_channels` 表从来没有 `updated_at` 列（schema_registry 里只有
`channel_id/name/channel_type/created_by/created_at`，且 local_bus 建频道时
也只写 `created_at`，无任何代码维护 `updated_at`）。SQLite 抛
`no such column: c.updated_at`，被 `except` 吞成 `logger.debug`，结果
`bus_channels` 上下文静默缺失。改为 `ORDER BY c.created_at DESC`（表中唯一
存在的时间列）。同类事故的旁证见 [[message_bus_trigger.py]] :497 的注释。

## 2026-05-19 — Reply Discipline 段强化 Agent-to-Agent 简洁优先

新增两条规则：
1. 显式标注"对方是 agent 不是 human"，要 Agent 跳过寒暄、首选一句话 /
   单数字 / 单 list 这种最小回复形态。
2. "Substance-empty → 明确选静默" — 没新信息时不要 call `bus_send_*`，
   直接结束这轮；平台按 `[NO_REPLY]` 处理，unread 游标按正常方式推进。

跟 [[prompts.py]] (chat_module) 配对：chat 路径强调"对人要温暖"，bus
路径强调"对 agent 要极简"。两边各自收紧自己的边界。

# message_bus_module.py — MessageBus Module 主体

## 为什么存在

`MessageBusModule` 是 `XYZBaseModule` 的子类，遵循 Module 热插拔协议。它负责两件事：在每次 AgentRuntime 执行前（`hook_data_gathering()`）把 MessageBus 的状态（未读消息、频道列表、已知 Agent）注入上下文；在 MCP 服务器里暴露 MessageBus 操作工具供 LLM 调用。

如果没有这个 Module，Agent 就对 MessageBus 的存在毫无感知——不知道有新消息，也不能主动发消息或管理频道。

## 上下游关系

**被谁加载**：ModuleService 根据 `MODULE_MAP` 在 AgentRuntime 初始化时按需加载；MCP 服务器通过 `module_runner.py` 启动时实例化。

**调用谁**：实例化一个 `LocalMessageBus`（通过 `get_db_client()` 取 backend）；调用 `_message_bus_mcp_tools.py` 里的工具函数暴露 MCP 工具；在 `hook_data_gathering()` 里调用 `bus.get_unread()`、`bus.get_channel_members()` 等取数据。

## 设计决策

Instance 级别是 **Agent-level**（`is_public=True`），即每个 Agent 有一个全局共享的 MessageBusModule 实例，不是每个 Narrative 各自一个。这是因为 MessageBus 是 Agent 级别的通信能力，不需要按 Narrative 隔离。

`hook_data_gathering()` 中注入的消息格式以 `[MessageBus · {from_agent}]` 开头（类似 Matrix 的 `[Matrix · ...]` 前缀），让 continuity.py 的 `_extract_core_content()` 能识别并提取核心内容。如果这个前缀格式改变，需要同步更新 `continuity.py` 的处理逻辑。

~~在 `WorkingSource.MESSAGE_BUS` 触发路径下，`hook_data_gathering()` 注入的信息会更精简。~~
**2026-08-11 更正:这句是反的,而且从来没实现过。** `hook_data_gathering` 里没有任何
`working_source` 分支 —— Known Agents / Your Channels / Unread Messages 三份列表对
**每一个**场景一视同仁地注入,包括 owner 私聊、job、以及各 IM 渠道的轮次。本文件里
唯一读 `working_source` 的地方只用来给 input 加 `[MessageBus · …]` 源标签。

这句反话的代价是它掩盖了真实形状:团队房间的未读因为读游标死锁而无限堆积,再被原样
灌进该 agent 所有场景的上下文。游标已在 2026-08-11 修复(见 `local_bus` 与
`message_bus_trigger` 的同日条目),注入范围本身保持不变 —— 那是「顺带瞥一眼群里
动静」的能力所在,污染是死锁的症状,不是注入设计的错。

## Gotcha / 边界情况

`MESSAGE_BUS_MCP_PORT = 7820` 是该 Module 的 MCP 服务器端口，如果其他 Module 使用了这个端口会发生冲突。新增 Module 时注意检查端口占用。

Module 实例是 Agent-level 的，但 `hook_data_gathering()` 运行时的 `agent_id` 来自 `ctx_data.agent_id`——同一个 Module 实例可能为不同的请求提供服务，不要在实例变量里缓存 agent_id 相关的状态。

## 新人易踩的坑

`MessageBusTrigger`（外部驱动 Agent 处理消息）和 `MessageBusModule.hook_data_gathering()`（Agent 主动查询 bus 状态）是两个独立的机制，可以同时工作。不要误以为开启了 Module 就不需要跑 `MessageBusTrigger`——前者是"Agent 主动感知 bus"，后者是"bus 主动推送消息给 Agent"。

## 2026-08-11 — 未读注入:窗口取最新、总数单独查、源标签取对头

抓取改为把上限**下推进查询**并取**最新** N 条。此前是拿全量再 Python 切片,切的是
oldest-first 列表的头部 —— 拿到的是积压里最古老的那些;再叠加 team 房间读游标永不
推进,这个窗口是**冻结**的:同样 20 行,一轮又一轮,以"房间当前状态"的名义呈现。

`bus_unread_total` 是新的 extra_data 键:查询加了 LIMIT 之后,`N unread (showing M)`
里的 N 不能再是结果的 `len()`,否则 N 恒等于 M。

`unread_models[0]` → `[-1]`:那行注释写着 "most recent trigger",而列表是 oldest-first,
`[0]` 是积压里**最旧**的一条。注释和代码指着相反的两端。

## 2026-08-12 — 两句站不住的规则,和一个终于有人读的字段

**「群聊里你只看得到 @ 你的消息」被改写。** 这句在 `_static_instruction_parts` 里,
对自建 bus 群是**对的**,对 team 房间是**错的** —— 后者的 turn prompt 带着整段房间
scrollback,还在十行后明说"每个成员都看得到本房间每条消息"。同一个上下文窗口里两句
互相矛盾的话。

不能加房间类型分支:这一段需要跨轮字节稳定(R4 缓存),分叉就毁掉它存在的理由。所以
改成**在所有房间都成立**的说法:「@mention 决定谁**被唤醒**,不决定谁**看得见**;
你在某个房间能读到什么,由那个房间自己的 prompt 说明」。房间的事实交给唯一知道答案
的地方。

**「未回复的消息会重新出现」收窄到私聊。** 在 DM 里未读列表就是队列,不回确实等于
延后。而 team 房间靠渲染投递,一轮跑完就算已读(2026-08-11 的游标修复),不收窄就是
一条平台在 agent 加入团队那一刻起就不再遵守的承诺。

**`via_team` 终于有消费者。** 它被算出来后全仓无人读。Known Agents 这份列表把队友和
owner 名下其它所有 agent 混在一起,agent 想找人帮忙时分不清"已经和我在一个房间里"
和"素不相识、要冷启一条 DM"。现在渲染成 `(teammate)`。

## 2026-08-12 (review 后) — 被推翻的那句话在同一个文件里还有第二份

静态段那句已经收窄成「**未回复的私聊**会重新出现」,但一百行之后的 volatile 块仍然
无条件输出「Ignored messages stay unread」—— 而它就贴在 `### Unread Messages` 的表头
下,那个列表里**混着 team 房间的未读**。对 team 房间它现在是假的(跑完一轮就推进
`last_read_at`,回不回复都一样)。

修一份留一份,正是这次改动开篇要消灭的「同一个上下文窗口里两句矛盾的话」,只是位置
挪了一百行。铁律 #8 说的"加功能时顺手扫一遍相邻代码",这次没扫到。
