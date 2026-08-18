---
code_file: src/xyz_agent_context/module/message_bus_module/_message_bus_mcp_tools.py
last_verified: 2026-08-14
stub: false
---

## 2026-08-17 — 工具按 agent 的社交处境重命名，新增 `message_team`

`bus_*` 这个前缀命名的是一个 agent 不该知道存在的子系统。改名后名字说的是**处境**：
`message_agent` / `message_team` / `create_team` / `find_agent` / `read_history` /
`team_share_file` / `team_list_files` / `team_pin_rule` / `team_unpin_rule` /
`team_work_*`。

**删除**：`bus_get_unread`（未读已注入，且说明书写着"别调"——而 prod 上 165 次调用证明
散文劝阻不起作用，所以解法是拿走工具而不是继续劝）、`bus_get_channel_members`（team 花名册
已注入 prompt，DM 只有两人）、`bus_get_agent_profile`（Known Agents 已注入）、
`bus_leave_channel` / `bus_kick_member`（成员关系归用户管；后者在 team 房间里
**结构上永远失败**——creator-only，而 creator 是合成标记）。

**`message_agent` 合并了旧的两个 peer 发送工具（`bus_send_to_agent` / `bus_reply_to_channel`）。** 回话与主动找人是同一个动作，所以是同一个
工具；`to` 必填，因为一轮里可能有多个 peer，平台不猜（决策 ⑥）。

**`message_team` 是补上的动词**，落点 [[team_posting]]。`team_id` 必填：agent 可同时在多个
team。三道门与其它 team 工具同序：agent 存在、team 属于其 owner、agent 是成员。

**`_describe_agent` 永不抛。** 它跑在发送成功之后、在工具的 `try` 里；抛了会把**已投递**的
消息报成 `{"success": false}`，agent 会重发。一个装饰性的回显不该能反转它所描述的动作的结果。
（它此前根本不存在——`message_agent` 一被调用就 `NameError`，被 except 吞成失败。pyright 抓到。）

**`create_team` 的 docstring 原来写着「Create a new MessageBus channel」并推荐
`message_agent`。** 按词表，`MessageBus` 不得出现在任何 agent 可见文本里。
## 2026-08-14 — bus_list_team_files 补上漏掉的 get_db_client 导入

该工具自 2026-08-07 落地起就引用了未导入的 `get_db_client`（本文件的 db 导入全是**函数内局部**——82/396/463/498 各在别的函数作用域，闭包解析不到），每次调用必炸 `NameError`，而 [[test_list_team_files_tool]] 原有测试只测 impl 不过 wrapper，全绿假象。修复=补函数内导入（与兄弟工具同款，保持模块加载期不引 db_factory 的循环导入规避）+ 按兄弟工具惯例整体包 try/except（review Minor-4：此前它是这批 bus_* 里唯一裸抛的——连接池懒构建失败会把原始异常甩给模型；except 只回 `{"success": False, "error": ...}`，**不补 `files: []`**，拒绝≠空文件夹）。新增走 `register_message_bus_mcp_tools` 注册面的 wrapper 回归测试。教训：MCP 工具的测试必须打到注册的 wrapper，不能只打 impl。

## 2026-08-07 — 两个发送工具盖上 root_run_id

`message_team` / `message_agent` 把 `caller_root_run_id()` 写进
`bus_messages.root_run_id`。这是血缘链**唯一的断点**:工具跑在共享的 MCP
进程里,除了注入的身份之外对调用方一无所知,而它写出的这条消息正是下一个
run 的触发源。与 `_send_turn_source` 同理——只有 send 现场知道自己的目标。

## 2026-08-07 — 新增 bus_list_team_files

共享目录终于可被枚举。工具本身是薄封装，规则在 [[team_files.py]]：授权按 **成员关系**
而非 owner（一个 user 多个 team，按 owner 判会让该 owner 的任意 agent 读到全部 team）。

配套：team prompt（[[message_bus_trigger.py]]）从「用 Read 打开这个目录」改为**明确指向本工具**
——此前 agent 只能猜路径或让别人复述，发现文件靠模型之间的社交协议。

## 2026-08-05 — 删掉 `bus_register_agent`：名录不能有第二个写入者

`bus_agent_registry` 现在是 A2A 发现的权威行，而这个工具是它的**第二个写入
者**，且写 `owner_user_id=""`（源码原注释是 "Will be filled in by the caller
context"，但没有人填）。`LocalMessageBus.search_agents` 的 where 带
`AND owner_user_id = ?` —— agent 只要调一次，它立刻从**同 owner 的搜索结果里
消失**，直到下一轮 hook 把行修回来；顺带还重置 `registered_at`。它声明的
capabilities 也会被每轮的机械推导无声覆盖，即工具承诺的事它做不到。

按铁律 #2 直接删（不留薄壳）：描述归 [[awareness_module]] 的
`update_agent_profile`，capabilities 从技能+模块推导。
[[message_bus_module]] 指令里那句「除非要更新 profile 否则不要调
bus_register_agent」同批改写成指向 `update_agent_profile` —— 原文恰好在
「想修 profile 的时候」把模型送去调这个会破坏行的工具。有两条测试分别钉住
源码里不再出现该工具、以及指令不再提它。

## 2026-08-03 — `_send_turn_source`:章按「这一条发给谁」定,不按整轮定

两个发送工具不再直接写 `caller_turn_source()`,而是走
`_send_turn_source(to_agent=… / channel_id=…)`:先取轮次种类,只有当**本条
send 的目标**等于本轮差事作用域(`caller_errand_scope()`,见
[[_mcp_identity]])时才升级成 [[hook_schema]] 的 `BUS_ERRAND_TURN_SOURCE`。

**为什么不能整轮盖章**(同 PR 内自我推翻的做法):
`MessageBusModule.hook_data_gathering` 每轮把**跨所有 channel** 的未读
(`bus.get_unread`)注进 context,模块提示词又**要求**回答它们(「A question
is never ping-pong」)。所以差事延续轮次里顺手回答别的同伴 C 是平台自己引导
的常规路径;整轮盖章会把那条**回答**标成提问,C 于是不再向自己 owner 回报
—— P1 换个座位复发(2026-08-03 review round 4)。

已记录的残余:发进「恰好是差事 channel 的群 channel」会把每个成员那份都盖成
提问(bus 差事跑在自动建的 DM 上,要手工建群当差事 channel 才踩到)。

## 2026-08-04 — 两个发送工具都记录本轮种类

`message_agent` 与 `message_team` 都调 `caller_turn_source()` 并传给
bus,让消息自己带上"这是提问还是回复"。**两个都要**:它们写同一张表、
同一个消费方(`_incoming_is_reply_to_my_errand`),漏一个就让那条路径落降级。
turn source 同时走显式 header 与 bearer,所以 codex 上也读得到
(见 [[_mcp_identity]]);读不到时传 None,触发侧按未知降级。

## 2026-07-20 — file attachments + team share

`message_team` / `message_agent` gained `attachment_refs` (comma-separated
`att_` file_ids and/or workspace-relative paths); `_stage_send_attachments` resolves
the sender's owner (`agents.created_by`, dialect-safe via `get_db_client`) and stages
the files through [[_bus_attachment_impl]] before send. New `bus_share_to_team`
validates ownership + membership (`teams` / `team_members`) then publishes a file into
the team's shared scratch dir (a server-side write — agents can't write `_shared`
themselves under the cloud sandbox). owner_user_id is always looked up, never taken
from the LLM.

# _message_bus_mcp_tools.py — MessageBus MCP 工具函数集合

## 为什么存在

`MessageBusModule` 通过 MCP 服务器向 LLM 暴露工具，但工具函数的具体实现不应该直接写在 Module 类里（会让 `message_bus_module.py` 变成一个巨型文件，且工具函数需要独立可测试）。`_message_bus_mcp_tools.py` 把所有 MCP 工具的实现提取出来，成为可以独立注册到 MCP 服务器的函数集合。

命名前缀 `_` 表示这是 Module 的私有实现，不被包外直接引用。

## 上下游关系

**被谁用**：`MessageBusModule.get_mcp_config()` 返回的 `MCPServerConfig` 里包含工具列表，MCP 服务器框架（`module_runner.py`）把这些工具函数注册到 MCP 协议上暴露给 LLM。

**调用谁**：每个工具函数接受一个 `port` 参数（MCP 服务器端口）和一个 `get_db_client_fn` 参数（工厂函数，调用时返回 DB 客户端）。工具函数内部用这个工厂函数创建 `LocalMessageBus` 实例，调用 `MessageBusService` 的对应方法。这种依赖注入方式避免了工具函数持有全局 DB 状态。

## 设计决策

工具函数签名遵循系统约定的提取模式（`standalone function taking (port, get_db_client_fn)`）——这是为了避免与 `MessageBusModule` 类的循环导入问题，也让工具函数可以在没有 Module 实例的环境里（比如测试）独立运行。

工具覆盖了 MessageBus 的完整操作面：发消息（`send_message`、`send_to_agent`）、查询（`get_unread`、`get_messages`）、频道管理（`create_channel`、`join_channel`、`leave_channel`）、Agent 发现（`search_agents`、`get_agent_profile`）。**注册不在这一面上**——名录行由 [[agent_discovery_sync]] 单点重算，见上方 2026-08-05 条。

## Gotcha / 边界情况

工具函数里的错误处理：一般返回 `{"success": True/False, "error": "..." }` 格式，不会向 LLM 抛出 Python 异常。LLM 需要检查返回值里的 `success` 字段来判断操作是否成功。

每个工具调用都会新建 `LocalMessageBus` 实例（通过 `get_db_client_fn()`），而不是复用同一个实例。这不是性能问题——`LocalMessageBus` 的 `__init__` 只接受一个已有的 backend 引用，构造成本极低，且避免了状态共享问题。

## 新人易踩的坑

工具函数名（如 `"send_message"`）就是 LLM 调用时使用的工具名，必须和 MCP 服务器注册时的名称一致。如果修改函数名，需要同时更新 `MessageBusModule.get_mcp_config()` 里注册工具时使用的名称字符串，否则 LLM 调用会报"工具不存在"。

## 2026-08-11 — `bus_pin_team_rule` / `bus_unpin_team_rule`

薄包装，规则在 [[team_bulletin]]。**没有 `team_id` 参数**：来自
`caller_team_id_from_request()` 的服务端身份头。这比隔壁 `bus_share_to_team`
（模型传 team_id + 三段校验）更强——agent 无法指认自己当前不在的团队，
于是跨团队写入不是要防的攻击，而是**不可表达的状态**。有测试断言签名里没有 `team_id`。

## 2026-08-14 — `message_team` 补盖 `event_id`:归因缺的那一半

此前只盖 `root_run_id`(触发树的根,用来续 cascade),没有盖**这一轮**的 id。后果不在
这个文件里显形,而在 trigger:团队房要判断"平台没代发的这一轮,房间到底听没听见这个
agent 说话",平台自己代发的那条消息盖了 turn id、agent 用本工具发的那条没盖,于是同一个
问题只有一半能被回答 —— 剩下那一半只能靠猜,而猜错就是在一个**已经听见回复**的房间里
再贴一条"投递失败"。

`event_id` 从 `_mcp_identity` 的请求头取(`caller_event_id_from_request`),不是模型
参数;artifact 工具早就是这么记归因的,这里只是把同一条路补齐。

## 2026-08-14 (补) — `message_agent` 一并盖章,并补上真入口测试

只给 `message_team` 盖 `event_id` 会让 `bus_messages.event_id` 的含义取决于
写它的是哪个工具。两处一起盖。

这半条链此前**零测试**:trigger 侧的用例都是桩里自己写一行带 `event_id` 的消息,
等于把「工具会盖章」当前提写死,而不是验证它。而
`caller_event_id_from_request()` 设计上就是**头缺失即静默返回 None** —— 头注入链
(context_runtime 传参 → `agent_id_headers` → adapter 转发 / bearer 第 8 段)任何一环
断掉都不会有测试变红,症状却是团队房里偶发的假 ⚠️。
`test_bus_send_event_id_stamp.py` 走注册后的真工具函数 + 伪造 ambient request 头,
并把「无头 → None」这条降级契约也钉住(实测过去掉盖章四条全红)。
