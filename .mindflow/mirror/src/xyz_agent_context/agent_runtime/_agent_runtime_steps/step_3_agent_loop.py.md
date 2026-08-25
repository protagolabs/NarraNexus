---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_3_agent_loop.py
last_verified: 2026-08-25
stub: false
---

## 2026-08-25 — 兜底限流的键真正含上了 `agent_id`

键是 `f"{agent_id}:{channel}:{room_id}"`。（本页 2026-08-25 早先那条写的
`f"{channel}:{room_id}"` 已过时。）

值得记的是**它上一次「修好了」其实一行都没生效**：参数加了默认值
`agent_id: str = ""`，两个调用点一个都没改，于是所有调用继续走空 agent_id，
键实际没变。默认值正是把这个错误藏起来的东西——与本 PR 在
[[_entity_updater.py]] 里刚吸取的教训同一条（铁律 #2：默认值让「忘记传」
变成静默降级）。现在参数**必填**，两个调用点都显式传。

今天这条限流只在 DM 生效（房间里只有我方一个 agent，`room_id` 天然不撞），
所以键含不含 `agent_id` 行为一样。但这张 map 是模块级、被进程里所有 agent
共享，一旦限流放宽到 DM 之外，agent-blind 的键会让 A 的三次兜底静默关掉 B
在同一房间的兜底——与 [[ingress_guard.py]] 会话键漏 agent_id 完全同形。

## 2026-08-24 — DM 兜底加两道前置判断

`no_reply_im_dm` 会用 helper LLM **替 agent 编一条回复发出去**。它此前只问
一个问题：有没有调过回复工具。8/14 事故里它没上场，但换个剧本它自己就是
一台乒乓引擎——每条兜底回复落到对面都是一条新的入站消息。

**门 1 · `agent_peer_no_fallback`**：`message_bus` 从一开始就被排除在兜底
之外，理由白纸黑字写着「不能替 agent 回答 peer agent」。但 A2A 对话也会
走 IM 渠道（8/14 就是两个 agent 的 NarraMessenger DM），那条路上这个排除
从来没生效过。agent 选择不回复就是选择不回复，替它编一条是机器对话变成
永动机的起点。

**门 2 · `fallback_rate_limited`**：兜底是为**疏忽**准备的（本来想回，忘了
调工具）；疏忽是偶发的。稳定产出疏忽的对话，不会因为第四条编出来的回复而
变好。

**信号怎么上来的**：决策函数原本只有四个入参，拿不到 `ParsedMessage` /
`channel_tag` / db。给 `ChannelTag` 加了 `is_agent_peer`，由
[[channel_trigger_base.py]] 的 `is_agent_peer` seam 填。它**和 room_type 走
同一个信封**——这样一个忘了填信封的渠道是整个失去 DM 兜底，而不是拿到一个
半武装的版本（room_type 当年就栽在这上面，见 2026-08-06 那条）。

**计数器也会被清理**（预审补）：`_recent_fallback_count` 只清它被问到的那个
键，所以一个只兜底过一次、之后再没消息的房间会留到进程结束。与
[[ingress_guard.py]] 的 `_sessions` 是同一个形状的洞，投递计数时顺带扫一遍。

**计数器进程内即可**，键 `f"{channel}:{room_id}"`，抄
[[background_llm_alerts.py]] 的 `_notify_cooldown` 惯例。它是抑制启发式，
不是安全关键冷却——真正扛重启的止血是 [[ingress_guard.py]] 的落库熔断。
**在投递成功时才计数**：渠道没发出去的兜底从没落到对面，不该花掉这个对话
的额度。

## 2026-08-21 — `_ensure_executor_for_run`：判决在这一层算

新增模块级 `_ensure_executor_for_run(user_id, run_id)`，替掉原来直接调
`ensure_executor(ctx.user_id)` 的那一行。

**为什么判决在这一层**：这里是唯一同时知道"哪个用户"和"哪个 run 在问"的地方。
到 step 3 的时候，**提问者自己的 events 行已经是 running**，不把自己排除掉判决就
恒为"忙"，stale 镜像永远滚不动 —— 那是把一种静默故障（掐 run）换成另一种（旧
executor 拿到空 MCP 集还不报错）。[[broker_client.py]] 是传输客户端，不拥有这个
决定的任何一部分。

**为什么抽成一个有名字的函数而不是三行内联**：这个关键字参数的性质是"漏掉它，本
进程行为一模一样，而 broker 那侧静默回到 2026-07-31 的行为"。内联的话没有任何测试
会因为它消失而变红；抽成 seam 就能钉住（`test_step3_hands_the_stale_replace_verdict_to_ensure`）。

**没有 broker 时先短路返回 None**：判决是**实参**，会先于 `ensure_executor` 内部的
`if not base: return None` 求值。不短路的话，local / desktop / 静态
`AGENT_EXECUTOR_URL` 这三种形态每一轮都会白查一次 `events`，拿到的布尔值立刻被丢掉
（铁律 #7：两种运行模式不能互相加税）。判据用 `broker_url()` 而不是
`executor_seam_active()` —— 后者把静态 URL 也算作 active，而那条路根本不调 broker。

**这个 helper 必须放在 `@timed("step.3_agent_loop")` 之前**：它一度被插在装饰器和
`async def step_3_agent_loop` 之间，于是装饰器绑到了它身上 —— 整条 pipeline 最重的
一步静默失去计时，而那个指标名开始上报 ensure 的耗时（几十 ms），下一个查"step 3
慢"的人会得出"step 3 不是瓶颈"的结论。
`test_step_3_keeps_its_timing_decorator` 用 `__wrapped__` 钉住。

## 2026-08-17 — team 投递阶段整块删除；来源声明在这里合成

**删除** `_team_room_delivery_phase` / `_should_deliver_team_reply` /
`_post_team_room_reply` / `_team_room_reply_frame`（共 155 行）及其调用点，
`AgentRuntime.run` 的 `on_plain_text_delivery` 参数与 `StepContext` 上的同名字段一并删除。

team 房间此前是**唯一**「agent 的纯文本就是回复、由平台代发」的表面。这个例外关不住：
框架 constitution、ChatModule instruction、bus module 规则都在陈述通则，而三者里每轮
只能关掉一个——PR #311 被评审打了六轮，打的全是由此长出来的矛盾。房间现在收工具调用
（`message_team`），本步骤因此无事可判：agent 有没有在房间里说话是 bus 里的一个事实，
由 trigger 直接读（`has_message_from_turn`）。原来这里权衡的 @mention 解析、级联上限
及其播报、errand 记账，全部搬到 [[team_posting]]——它们是「往房间发帖」的属性，而不是
「恰好拥有投递权的那一步」的属性。

**新增**：`origin_declaration=render_origin_declaration(ctx.working_source, ...)`。

`_im_reply_tool_name` 的过滤改用 `is_owner_tool`：它此前只写死排除 `notify_owner`，
默认 handler 开始同时列出两个名字的那一刻 `reply_owner` 就溜了过去。


## 2026-08-13 — 平台来源绑定：stamp identity 上 provider 配置

紧接 MCP token stamp 之后，`identity_token` 非空则调 `bind_platform_identity(identity_token)`
把同一 token 盖到本 turn 的 provider 配置上（同 task context，早于 driver 快照）→ 随
`provider_configs` 过线到 executor，出站 LLM 调用带 `X-NarraNexus-Identity-Token`（仅自家网关）。
网关验签在**已部署且开启**的地方（deploy `staging` #20 的 `_enforce_identity`，`dev`/`main` 尚无；
且需 `NX_IDENTITY_VERIFY_MODE`=audit/enforce，**默认 off**）；其中 `audit` 只验签+记日志、照样放行，
**唯 `enforce` 才 403**，所以「被外带的钱包 key 到平台外作废」只在 `enforce` 成立。三者不全满足时该头是无害
no-op。与 MCP 面的 [[identity/tokens]] 复用同一 broker token。

## 2026-08-10 — dispatch 时 stamp MCP 身份 token（蓝图 P1）

`_dispatch_identity_token(ensured, user_id)`:云取 ensure() 返回的 broker 签名
token(每 run 新鲜);本地在 `NX_MCP_AUTH_MODE != off` 时才让进程自签
([[identity/tokens]] LocalEphemeralIssuer)——默认 off 时零 keygen 零文件写,
铁律 #7。**云端绝不自签**(review #1):`ensured is not None or is_cloud_mode()`
时无 broker token 就返回 None——进程内临时签名对不上 mcp 挂载的部署公钥,
audit 期会把「谁还没 token」的核心测量污染成 invalid 噪音,enforce 期则全站
401;测试钉住 audit 模式下旧 broker/云无 broker 两种形态都不 stamp。选中的
token 经 `stamp_identity_token` 原地写进 `ctx.mcp_servers` 的 headers
(bearer 第 **9** 位——与 #255 的 team_id/event_id 撞位后按「先到 dev 者得位」
让位,stamp 重建时透传全部既有字段,漏一个=codex 通道静默丢该事实):放在
TurnInput 之后是**故意的**——turn_input.py 文档明言 mcp_servers 按引用传递、
"step_3 merges into mcp_servers before the call",本处沿用同一契约。云 token
只在 ensure 后存在,所以 stamp 不能提前到 context_runtime 盖章处。测试:
`test_step3_identity_stamp.py`。

## 2026-08-10 (review 修正) — 授予收窄到本回合 team

改用 [[workspace_paths.py]] 的 `turn_accessible_roots`，team 取自
`trigger_extra_data["bus_team_id"]`（与 MCP 身份 header 同源）。此前授予的是整棵
`_shared`——该 owner 名下**每一个** team 的目录，且**每个回合**都授予。

字段随之改名 `extra_readable_roots` → `extra_accessible_roots`：它不是只读的。

## 2026-08-07 — 授予 per-user `_shared` 为额外可读根

组 TurnInput 时把 `{base}/{user_id}/_shared` 作为 `extra_readable_roots` 传下去，让
NexusPower 的 confinement 放行团队共享目录与 bus 附件（此前 prompt 让读、框架层拒绝，
且与 claude/codex 行为不一致——见 [[policy.py]]）。

范围性质：`_shared` 在**本 user 根之下**，而 message bus 禁止跨 user，故这不授予任何
跨 user 访问——正是 per-user Executor 已经挂载的同一棵子树。


## 2026-08-06 — auto review 收口（PR #247 两轮意见）

review 收口：framework_override 过 _framework_override_viable 守卫——与 nexus adapter 的 _resolve_provider **结构同构**——不止抄两个硬失败条件（OAuth 凭据 / 双槽均无模型），还抄 claude-first 短路优先级：claude.model 非空时 codex 永不被咨询，oauth claude + 有模型的 codex 也判不可行（第三轮 review #15 修正），不可行时保留 slot 解析并告警，语音轮经 legacy finalize 链自然降级；覆盖必要性（AGENT_REPLY_DELTA 表达流仅 nexus_power 有）与不可用时的降级路径以此为准。

## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

framework 解析后允许 profile.framework_override 钉框架（voice 需要 NexusPower 的流式/expressive 接缝）；TurnInput 携带 turn_profile。

## 2026-08-07 (三次) — 哨兵改成「剔除后判空」，不再靠等值

上一条引入的 `NO_REPLY_NEEDED_SENTINEL` 用的是**精确等值**比较。指令写的是「只输出
这串、别的都不要」，但 helper 跑在**用户自己配的 provider** 上（铁律 #15：平台不管
用户选什么模型），所以给哨兵加引号、加句号、前面垫一句解释都在合法范围内——等值一落空，
`text` 非空，`<<<NO_REPLY_NEEDED>>>` 这串**内部标记就被原样投递进用户的 IM 私聊**。
这类失败的输出用户直接看得见，比「多回一句话」更丢人，而健壮性必须在我们这侧。

改为 `in` 判定 + 无条件剔除：命中就把哨兵删掉，残余非空则投递残余（哨兵永不可能到达
对端），残余为空则复用既有静默出口。

**比 review 建议的一行多一道**：剔除后剩下的可能只是模型给哨兵套的标点（`"…"`、
末尾 `。`），非空但不是回复，投递它只比投递哨兵好一点。所以再判一次
`re.search(r"\w", text)`——`\w` 把 CJK 也算实义字符，问的是「这里还有没有内容」，
而不是去枚举各种引号和标点形态。测试里那两个变体（`"哨兵"`、`哨兵。`）就是这么发现的。

## 2026-08-07 (二次) — review 收口：沉默豁免可达、severity 不再漂移、import 上提

三处，都来自 PR review：

1. **`NO_REPLY_NEEDED_SENTINEL` + `_FALLBACK_IM_DM_EXTRA`**：私聊协议承诺了一个窄
   豁免（对方那条是纯确认且无新内容可加则不回），但兜底的判据只有「有没有调表达
   工具」——模型**正确地**对「谢谢」保持沉默恰好就是没有工具调用，于是兜底照样写一条
   发出去，**该豁免在生产上永远不生效**。现在 `no_reply_im_dm` 模式的指令多一段：
   命中纯确认就只输出哨兵串，`_stream_fallback_recovery` 见到它把 text 归零，复用
   既有的静默出口（不投递、不写 synthetic 帧）。prompt 说的和平台做的从此一致。
2. **`_has_organic_reply` 的 `working_source` 去掉默认值**（铁律 #2）。默认 `"chat"`
   让 severity 那个姊妹调用点悄悄保留了本函数存在就是为了消除的漂移：一轮 IM 对话
   已经通过 `wechat_send` / `lark_cli` 回复过、随后撞上 executor-infra 失败 →
   被判「从未说话」→ `severity="fatal"` → `had_fatal_error=True`，**一轮实际已交付的
   对话记成失败轮次**，用户前端拿到硬「retry」而不是 warning 徽章。两个调用点现在
   都显式传值。
3. **六处函数内延迟 import 提到模块顶层**。核查过无循环依赖：
   `channel.message_source_handler` / `channel_sender_registry` / `channel_prompts`
   只依赖 stdlib + loguru，都不 import `agent_runtime`（反向依赖由
   `channel_trigger_base` 自己的延迟 import 解决）。其中一处在生成器体内，**每轮
   执行一次**。
4. 顺带：`_FALLBACK_NO_REPLY_INSTRUCTIONS` 正文里的
   `didn't call send_message_to_user_directly` 改成中性的「never called its reply
   tool」——IM 轮次里那个工具本就不是表达工具，给模型的事实前提是错的。

## 2026-08-07 — 真机测试揪出的两处收口（信封接线 + 决策可观测性）

同日 IM 私聊兜底（下一条）上线后用真 Telegram 私聊验，两个问题：

1. **信封接线错了，整个兜底是死代码。** `_channel_turn_envelope(ctx)` 里
   `getattr(ctx, "ctx_data")` 恒为 `None`——ContextData 是本步**新建**的、挂在
   ContextRuntime **输出**上（`context.ctx_data`），`ctx` 没这个属性。于是信封恒空、
   `is_direct_message` 恒 False，`no_reply_im_dm` 一次都不会触发。改为传 `context`。

   **函数级单测抓不到它**：函数本身是对的，错在调用点传了哪个对象。所以补的不是
   更多单测，而是下面的可观测性 + 一个跨层集成测试。发现方式是对读数据库：
   同一轮的 prompt 里写着 `Direct Message`，日志里却是
   `skip_reason='group_room_may_stay_silent'`。

2. **矛盾之前不可见。** 已回复的分支**不打日志**（原有行为），走错的分支看起来又像
   一次合法跳过。现在每轮无条件打一行
   `[FALLBACK] decision: mode=… skip_reason=… working_source=… room_type=…
   has_reply_kwargs=…`。prompt 与这个决策**读的是同一个 room_type**，所以
   「prompt 说私聊、决策说群聊」是信封坏掉的确切特征，一眼可见。这正是
   CLAUDE.md 事故教训 #4 要的 L2 级观测（不是「进程活着」，而是「它在做该做的事」）。

真机结论：第一层（协议分叉）三轮验证通过——prompt 逐字确认注入私聊协议、无群聊纪律，
模型对一句问候正常作答（旧协议下沉默才是「正解」）。修好后拿到正面证据：
`room_type='Direct Message' skip_reason='already_replied_via_tool'` ——
信封到位，且防重复发送那道闸在工作（模型自己回了，平台没有多发一条）。

**第二层的投递路径真机没触发过**：第一层修好后模型一直正常回复，这正是想要的结果，
但也意味着手动测试撞不到兜底。故补
`tests/agent_runtime/test_im_dm_fallback_delivery_e2e.py`——它第一次运行就抓到了
第三个 bug（见 `message_source_handler.py.md` 2026-08-07 条目：合成帧被渠道抽取器
误读成占位符 / 沉默）。

## 2026-08-06 — 兜底扩到 1:1 IM 私聊（推翻 2026-05-12 的门禁前提）

原门禁是 `working_source != "chat" → non_chat_trigger`，理由（2026-05-12 条目里
写着）「job/lark 有自己的回复通道」。**这个前提把「有回复工具」和「回复发生了」
混为一谈**：模型输出明文却没调渠道发送工具时，文本被丢弃、这轮记成 activity 行、
对端一个字也收不到。0802 微信工单就是这条。`message_bus` 仍排除——那半个理由
（不许回复同伴 agent，防 agent 间循环）今天依然成立。

- `_has_organic_reply(agent_loop_response, working_source="chat")` **改走
  `MessageSourceRegistry`**，不再硬编码 `send_message_to_user_directly`。
  **这是防重复发送的那道闸**：不改的话，一个正确调用了 `wechat_send` 的轮次会被
  判成「没回复」，兜底就会在**每一次成功对话**后再发一条 helper 写的消息。默认参数
  保持 chat 语义，既有调用点行为不变。权威与 `chat_module._delivered_to_origin`
  同源，两层不可能再漂移。
- `_should_run_helper_llm_fallback(..., is_direct_message=False)` 新增
  `"no_reply_im_dm"` 模式；跳过理由细分：`group_room_may_stay_silent`（IM 群聊——
  那里沉默是设计行为）、`fatal_no_invented_reply`（私聊轮次中途 fatal：chat 敢做
  `after_error` 是因为前端还会并排显示错误徽章，IM 对端**没有任何错误面**，
  给他一条自信满满、由半个念头合成的回复更糟）、原有 `non_chat_trigger`。
  「是不是真渠道」用 handler 的 `dedicated_trigger` 判定——把 lark/wechat/telegram
  与 `callback`/`a2a` 区分开，后者没有房间，报「群聊」是胡说。
- `_deliver_im_fallback_reply()` 经 `ChannelSenderRegistry` 真投递（chat 不需要
  对应物：那边把 delta 流给前端**就是**投递；IM 对端没有这个流，没人调渠道 API 的话
  回复只存在于我们数据库里，正是 0802 的形态）。**只有渠道确认成功才 yield
  synthetic 帧**——把「没发出去」记成「已回复」和我们正在修的丢弃明文属同一类谎报。
  永不抛异常。
- IM 分支刻意**不 yield `AgentTextDelta`**、synthetic 帧刻意**不标
  `send_message_to_user_directly`**（改标渠道自己的发送工具，见
  `_im_reply_tool_name`）。两者都会让这条回复出现在**主人**的聊天面板里，像是 agent
  对主人说了话；`chat_module._split_user_visible_response` 正是按这个工具名分流的。
- 文案复用 `_FALLBACK_NO_REPLY_INSTRUCTIONS`（`_fallback_instructions_for_mode`
  对非 `after_error` 一律返回它），因此 2026-07-30 那两条诚实性规则自动继承。
- 渠道事实经 `_channel_turn_envelope(ctx)` 从 `ctx_data.extra_data` 读通用键
  （`channel_room_type` / `channel_reply_kwargs` / `channel_tag`，由
  `ChannelTriggerBase` 放入）。**没信封 = 不是 IM 轮次 = 不兜底**，这是 chat/job/bus
  的安全默认。

测试：`tests/agent_runtime/test_im_dm_no_reply_fallback.py`（防重复发送、决策六
分支、投递五种失败形态）；`test_helper_llm_fallback_decision.py` 补 IM 群聊用例，
并把 `lark` 从 `non_chat_trigger` 参数化里移出（它是渠道，理由现在取决于房间类型）。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

TurnInput 组包新增 `agent_id=ctx.agent_id` 与
`expressive_tools=context.expressive_tools`(3.2 模块声明的投递面)。此前
适配缝空转:NexusPower 靠 server 名含 "chat" 猜回复工具、agent_id 恒
"agent"(2026-07-31 排查确认的生产缺陷)。

## 2026-07-30 (二次) — 兜底回复不许承诺没在做的事

`_FALLBACK_NO_REPLY_INSTRUCTIONS` 加两条规则:(1) 禁止任何"我来做 / 让我试试 /
稍等"式的进行中或即将开始的表述——**这条消息发出去,这一轮就结束了**,承诺永远
不可能兑现;(2) 当 `<this_turn_activity>` 显示 agent 只产出了意图、没有任何实际
结果时,必须直说没做成 + 给一条具体出路。同时把选 prompt 的内联三元表达式提成
`_fallback_instructions_for_mode(mode)`——这两段是**平台唯一替用户的 agent 张嘴
说话**的文本,值得有个能被测试钉住的接缝(见
`tests/agent_runtime/test_fallback_reply_honesty.py`)。

触发事件(2026-07-29 Jiaxi 报障):用户让 agent 看图写 Word,agent loop 一轮结束、
零工具调用,思考内容只有"我来用图像理解能力重新试试"。no_reply 兜底的指令是
"把 agent 本该说的话说出来",于是它忠实地把这句意图讲给了用户——用户等一份
没有任何东西在生产的文档。**一轮没干完是允许的**(铁律 #14 不强停、#15 不评判
模型);不允许的是**我们生成的文字**声称有活在干。区分点:管的是平台自己编的
话,不是模型的行为。

## 2026-07-30 — model_not_found 反哺探测嫌疑

fallback-skip 判定之后：raw_exception 路径直接看 `skip_reason_detail`，inline
路径扫 `ErrorMessage.action_reason`（response_processor 归因时写入），命中
`model_not_found` 就调 [[model_health]]`.report_agent_slot_suspect`（解析当前
agent slot 绑定→(source, protocol, model) 入嫌疑表，best-effort 永不抛）。
只有确定性 model_not_found 触发——余额/限流/5xx 归因不同，不会误伤。

## 2026-07-29 (二次) — helper payload 过滤原生回放行

`_build_helper_user_input` 的 history 过滤补两刀:role=tool 行(本就被排除)之外,
content 为空/None 的 assistant 行(原生回放的 calls-only 消息)也不进 prose
transcript——`str(None)` 曾会渲染出字面 `[assistant] None`。

## 2026-07-29 — 删除句柄机制(T5),−235 行

删掉的是:进程级并发闸门(`_resume_handles_in_use` + `threading.Lock`)、四重校验
`_resolve_resume_session_id`(叙事 / 指纹 / 工作路径 / 框架)、其包装
`_acquire_resume_session`、`_log_resume_cold`、lease 的 `try/finally`(退化为
`try/except`——那个 finally 存在的唯一理由就是释放 lease)、`resume_fingerprint()`
调用、`cli_config_fingerprint` 伴随字段、`TurnInput.resume_session_id` 传参。

**为什么整套都不需要了**:[[transcript]] 让 adapter 每轮自己写 transcript、用全新
uuid4 resume。于是没有存下来的句柄要查、没有东西会过期(校验的全部目的)、也没有
共享句柄会被两个 run 同时claim(lease 的全部目的)。

**T2 实测确认它在空转**:日志里 `resume decision: RESUME cli_session=…` 存的句柄,
正是我们上一轮自己生成的 uuid4(step_4 把它当 CLI 签发的存下来了),而我们每轮都
会覆盖它。四重校验算出什么都不影响结果。

顺带:**R5(叙事锚点降级)整项作废**。它要解决的是"叙事切换 → 校验不通过 → 冷启动",
而现在既没有校验也没有锚点。实测里那次白付 55,308 全价的形态,结构上不可能再发生。

## 2026-07-28 — 并发 resume 守卫：同一句柄同时只许一个 run 持有（review FIX 1）

同一 agent 的两个 run 可以并发（用户在聊，同 agent+owner 的 JobModule
trigger 同时触发）。R2 之前二者都是冷启动、无共享外部文件；R2 之后二者会
**解析出同一个 cli_session_id 并各起一个 `--resume <同一 id>` 的 CLI**，
两个写者共用一份 session JSONL。这种失败**不匹配** R3 兜底的
"No conversation found" 谓词，会直接以硬错误冒出——即本次 feature 自己引入的
新危害，必须在此处闸掉。

- 新增进程内守卫：`_resume_handles_in_use: set` + `_resume_handle_lock`
  （`threading.Lock`），键 = 表唯一键同一三元组
  `(agent_id, platform_session_id, framework)`。
  `_try_acquire_resume_handle` / `_release_resume_handle` 是 test-and-set /
  释放。**输者立刻冷启动**（`COLD reason=handle_in_use`），**绝不阻塞等待**
  ——等待会把 resume 从优化变成依赖、并让一轮卡在另一个长 run 后面（铁律 #14）。
- 为什么用 `threading.Lock` 而不是 asyncio.Lock / 裸 set：临界区是无 await 的
  test-and-set，asyncio.Lock 毫无收益还要像 [[db_factory]] 那样维护 per-loop
  注册表（asyncio 原语绑定创建它的 loop）；而本进程**可能有多个线程各自的
  event loop**（MCP 容器每模块一个线程 loop），危害与哪个 loop 驱动无关，
  所以守卫必须 loop 无关且共享。裸 set 单操作在 GIL 下原子，但 check-then-add
  这对不是，故取锁；锁持有微秒级、绝不跨 await，既不会死锁也不拖慢 loop。
- 新增 `_acquire_resume_session(...) -> (resume_session_id, lease_key)`：
  = 原校验闸门 `_resolve_resume_session_id`（保持纯校验、签名不变）+ 命中后
  才 lease。**lease 放在校验之后**：本来就不会 resume 的 run 不许挡住会
  resume 的那个。step_3 只许调这个 wrapper。
- COLD 日志格式抽成模块级 `_log_resume_cold(...)`，`handle_in_use`（在校验闸门
  之外决策）与闸门内八种 reason 共用同一形状，便于日志分析。
- **step_3 主体：resume 决策整块挪进 driver 那个 try**，末尾加 `finally` 释放
  lease。lease 必须**在 try 内**获取：若在 try 外获取，acquire 与 `try:` 之间
  任何抛错都会把键永久卡住（该 agent+session 在本进程余生里 resume 静默失效）。
  finally 覆盖四个出口——正常结束、`except Exception`、取消
  （CancelledError 属 BaseException，绕过 except 仍走 finally）、
  以及被弃用 generator 的 `aclose()`（GeneratorExit 落在 try 内的某个 yield）。
  释放函数**故意是同步的**：GeneratorExit 在飞时 await 会触发
  "async generator ignored GeneratorExit"，可能跳过释放。
- **连带修的真 bug**（否则上面的"airtight"是假的）：`@timed` 的
  asyncgen wrapper 用 `async for item in fn(...)` 转发，而 `async for`
  **不会关闭被迭代的 generator**——消费者 aclose 外层 wrapper 时，被包裹的
  step_3 只是挂着，它的 finally 要等 asyncgen GC finalizer 才跑（实测
  `aclose()` + `sleep(0)` 之后仍未释放）。已在 [[_timing.py]] 用
  `contextlib.aclosing` 修正：关闭立刻穿透。这条对**所有** `@timed` 异步
  生成器的 finally 清理契约都成立，不只 resume。
- **残留（已接受、fail-open）**：消费者用 `break` **丢弃**管线而不是关闭它
  （`agent_runtime.run()` 在取消时正是这么做的），`async for` 不会把关闭往下
  传，此时 finally 由 asyncgen GC finalizer 在一两个 loop tick 后跑（实测
  <10ms），而非同步。有界、自愈，最坏代价一次多余冷启动。若哪天要做到完全
  同步，需要在 `agent_runtime.run` 与 [[step_3_execute_path.py]] 两处的
  `async for` 上加 `aclosing`——本次刻意不动主管线取消路径。
- **刻意的局限**（措辞对齐 [[admission.py]]）：守卫是**进程内**的。云端今天
  orchestrator 单进程，一个守卫看得见所有 run；多副本部署需要按同一三元组
  做共享（Redis）守卫，上面两个 helper 就是那个缝。
- 测试：tests/agent_runtime/test_resume_concurrency_guard.py（lease 语义 +
  驱动真 step_3 验证正常结束/异常/中途 aclose 三条路径都释放，以及
  A 持有时 B 端到端冷启动）；tests/utils/logging/test_logging.py 补 aclosing 回归钉。

## 2026-07-28 — resume 决策 + TurnInput 注入（resume 化 R2/R3，dev 新结构重实现）

R1 只捕获；本条把查表/校验/注入接上（旧分支 be9c8ecd 的 step_3 部分在
dev 新结构上的重做——注入通道从裸 kwarg 换成 TurnInput 字段）：

- 新增模块级 `_resolve_resume_session_id(agent_id, session, framework,
  config_fingerprint, working_path, db_client)`（旧分支逐字移植）：开关
  （`settings.agent_loop_resume_enabled`）+ 句柄存在 + **三锚全符**
  （narrative / fingerprint / working_path）才返回存储的 cli_session_id；
  其余一律 None = 冷启动。**fail-open 到底**：查表/校验任何异常 → None +
  warning，优化永不打死轮次。铁律 #4：纯通用会话延续规则，无场景硬编码。
  每次决策恰好一条可 grep 日志：`[step_3] resume decision: RESUME …` 或
  `[step_3] resume decision: COLD reason=<flag_disabled|no_platform_session|
  fingerprint_unavailable|no_handle|narrative_changed|fingerprint_mismatch|
  working_path_changed|lookup_error:*> …`。
- 决策块置于 framework 解析之后、executor ensure 之前：canonical
  `cli_framework` 归一化与 `cli_config_fingerprint` 计算**上提到此处**
  （一次计算，决策与末尾 PathExecutionResult 组装共用；R1 原在组装处的
  重复计算段删除）。v1 只有 claude_code 走查询；codex 完全不碰。
- **TurnInput 构造移到决策块之后**（frozen dataclass，不能事后改），带
  `resume_session_id=`；driver_kwargs() 只在非 None 时发键（理由见
  [[turn_input.py]]——codex v2 的 ignored-kwargs WARNING 不被恒 None 字段
  刷屏）。
- PathExecutionResult 新增 `resume_failed=state.resume_failed` 透传——
  **无条件**（冷启动重试可能没报新 session_id，step_4 仍要删陈旧句柄）；
  CLI 句柄三伴随字段改为 `… if state.cli_session_id else None` 内联条件。
- 测试：tests/agent_runtime/test_resume_decision.py（九个决策用例）。

## 2026-07-28 — 不再现发会话票

step 3 开头那段「system-tier 运行就向网关 mint 一把 per-run key、注入
ClaudeConfig、finally 里吊销」的逻辑整段删除，连带 `gateway_unavailable`
的提前返回分支。

免费额度的凭据现在是用户 `user_providers` 里那张卡上的长期 key，和自带 key
走完全一样的 `provider_configs` 下发路径 —— step 3 对它没有任何特殊认知，
这正是目的。


## 2026-07-27 — driver 调用入参打包为 TurnInput（纯搬运）

3.4 组装 driver.agent_loop kwargs 的四个散落 local 收进
[[turn_input.py]] `TurnInput`，调用点改为
`driver.agent_loop(cancellation=..., **turn_input.driver_kwargs())`。
driver_kwargs() 复刻历史形状（含空值→None 归一），零行为变化。

## 2026-07-25 — PathExecutionResult 组装处补 CLI 句柄四字段（resume 化 R1）

组装前新增一小段：`state.cli_session_id` 非空时（只有 Claude 路径会报）填
`cli_framework`（framework_name 归一化到 canonical：claude→claude_code、
codex→codex_cli——存储键不能依赖用户 slot 恰好用了哪个别名）、
`cli_working_path=agent_working_path`、`cli_config_fingerprint` 经 ambient
`claude_config` 代理调 `resume_fingerprint()`。**指纹必须在 step_3 算**：本轮
的 per-task ContextVar 在此作用域保证还活着；step_4 不重算。fail-open：任何
异常 → None + warning，step_4 随之跳过持久化——resume 捕获永远不许伤害轮次。
本期只捕获不 resume（R2 的查表/注入还没接）。

## 2026-07-24 — 透传 `context.disallowed_tools` 到 driver kwargs（B++）

组装 driver.agent_loop kwargs 时新增 `disallowed_tools`（来自
[[context_schema.py]] `ContextRuntimeOutput.disallowed_tools`，即未绑定
channel 要求剔除的工具）。本地 SDK 侧与 WebSearch 守卫**合并**（见
[[xyz_claude_agent_sdk.py]]），remote 侧进请求体
（[[remote_agent_loop_driver.py]]）。codex driver 接受但忽略该 kwarg（本阶段
已知限制：codex 路径只有指令侧裁剪）。本文件纯搬运，无逻辑。

## 2026-07-23 — PathExecutionResult 透传 cache/num_turns(W1,纯搬运)

末尾组装 PathExecutionResult 时新增 `cache_read_tokens`/`cache_creation_tokens`/
`num_turns` 三项赋值(来自 state)。无逻辑变化;语义见 execution_state.py.md。

## 2026-07-23 — 免费额度网关会话票在此签发/作废（后端唯一正确的层）

免费额度改造：主钥匙只在 LiteLLM 网关容器，每次运行签一张会话票。**签票必须在本步
（后端 orchestrator）做，不能在 executor**——executor 跑用户可控代码、只收
`provider_configs`、绝不能持有网关 admin key。流程：驱动分发前调
`gateway_key_service.open_backend_session(db, agent_id)`：若 `provider_source=="system"`
就 mint 一张票并**写进 `ClaudeConfig` ContextVar**，随后
`executor_protocol.serialize_provider_configs()` 把它打包送到 executor，executor 只拿到
这张 scoped/可作废的票。返回 `(session, ok)`：`ok=False`（网关不可达/未配置）→ 直接
`yield ErrorMessage(error_type="gateway_unavailable", severity="fatal")` 并 `return`，
**绝不回退主钥匙、绝不用空占位 key 起子进程**。`session.close()` 在驱动 try 的 **`finally`**
里作废——run 生命周期界定、非定时器（铁律 #14）；非 system 运行整条链路是 no-op。硬崩溃
遗留孤儿由 executor-reaper 钩子回收（见 [[executor_reaper]]）。凭据细节见
[[gateway_key_service]]。

## 2026-07-22 — executor-infra 失败统一 surface + 审计 + try 边界上移

三处相关改动，收尾 OOM(-9/-6) 与 executor 不可达的"可读化 + 不被兜底掩盖 + 审计"：

1. `_record_oom_if_killed` → **泛化**为 `_record_executor_infra_event(db_client,
   user_id, error_type, error_str, output_already_emitted)`：用
   [[llm/failure.py]] `classify_executor_infra_failure` 判类，写
   `oom_killed`（-9/-6）或 `executor_unreachable`（[[executor_audit.py]]）。
   best-effort 永不抛，沿用原模式。
2. `_fallback_skip_decision` 返回**三元组** `(kind, reason, target_error_type)`：
   infra 命中先于 self-serviceable（typed/returncode 信号更确定）→
   `target=EXECUTOR_INFRA_ERROR_TYPE`；emit 分支按 target 选文案函数
   （`executor_infra_user_message` vs `self_serviceable_user_message`）。
   **OOM 从"故意 fall-through 到兜底"改为"surface + skip"**——不再被编造回复掩盖。
3. **try 边界上移**：`ensure_executor`/`wait_until_ready`/`get_agent_loop_driver`
   纳入同一 `try`。这样冷启动抛的 `ExecutorUnreachableError`（[[executor_errors.py]]）
   落到同一 except 走 infra 收尾，而不是逃出 step_3 变裸异常（issue ② 根因）。
   `PathExecutionResult` 结尾产出不受影响。
4. **severity 随"是否已回复"分级**（PR #133 review 连带修）：抽出
   `_has_organic_reply(agent_loop_response)`（复用到 `_should_run_helper_llm_fallback`）。
   infra/self-serviceable 的 raw_exception 分支：**若本轮已通过
   `send_message_to_user_directly` 回复过**（executor OOM/掉线可能发生在回复之后）→
   `severity="recovered_after_reply"`（warning 徽章、保留回复），否则 `fatal`。避免对
   已经拿到答案的用户显示"请重试"、也避免把"已回复但收尾失败"整轮记失败。配合
   [[loop/circuit_breaker.py]] 对 `infra_transient` 的熔断豁免，杜绝"平台抖动→冷却
   →拒掉用户按提示的重发"。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-07-14 — 兜底 skip 泛化:auth → auth + 确定性自助类（`_fallback_skip_decision`）

原本只有"inline auth 失败就 skip helper 兜底"一条（2026-06-11）。现在抽出纯
谓词 `_fallback_skip_decision(agent_loop_response, captured_error)`，把两条
error 路径都盖住:

- `("inline", None)`:`agent_loop_response` 里已有
  `error_type ∈ {auth_expired, config_actionable}` 的 fatal ErrorMessage
  （response_processor 在 loop 内已产出）→ 只 skip 兜底，不用再补消息。
- `("raw_exception", reason)`:loop 抛了 Python 异常，`captured_error` 有值
  但**还没有 ErrorMessage**，且 `classify_self_serviceable` 命中（类名保真，
  如 `ContextWindowExceededError`）→ skip 兜底 **并在此就地 yield** 一条
  fatal `config_actionable` ErrorMessage（否则该错误完全不可见）。
- `(None, None)`:非用户可修复失败 → 照常走 helper 兜底。

理由同 auth:context-window 这类确定性失败，agent 本体（工具/MCP/记忆）根本
没跑，兜底生成一条正常样子的回复是对事实的谎报——这正是"黑盒" P1 的根因。
分类器在 [[llm/failure.py]]，共享文案 `self_serviceable_user_message` 也在那，
避免 step_3 → response_processor 的循环导入。

## 2026-07-10 — `_resolve_agent_framework_name` 收缩为委托（单一 overlay）

原本这里手写了一份 agent_slots→user_slots 的 overlay。它现在**委托**给
[[providers/model_identity.py]] 的 `resolve_agent_model_identity(...).framework`——
同一份 overlay 既供 dispatch（选 driver）又供 prompt 的 "LLM Model" 行，二者不可能
再不一致。（PR #84：两份手抄 overlay 的判定曾漂移——prompt 侧漏了 `agent_framework`
非空这一条，在"有 provider 但 framework NULL"的 agent_slots 行上重新渲染出错误身份。）
`agent_runtime → agent_framework` 是合法 import 方向（本文件早已 import 该层）。
行为对 dispatch 不变，由 `test_resolve_agent_framework_per_agent.py` 兜底。

## 2026-07-09 — per-agent framework + owner bugfix

``_resolve_agent_framework_name`` is now keyed by ``agent_id`` (was ``user_id``).
It honours a per-agent ``agent_slots`` override that actually rebinds the agent
slot (has a ``provider_id`` — mirrors [[resolver]]'s overlay predicate so
framework and config never disagree), else falls back to the OWNER's
``user_slots`` (``agents.created_by``), else ``claude_code``. The call site was
fixed to pass ``ctx.agent_id`` instead of ``ctx.user_id`` — a latent correctness
bug: background triggers pass a trigger identity that isn't the owner, so the
framework could disagree with the owner-resolved config.

## 2026-06-18 — 冷启动 executor 先等就绪再驱动

冷启动分支（`ensured.cold_started`）发完 `executor.warming` UX 事件后,**先
`await wait_until_ready(executor_url)`(poll executor 的 /health)再驱动 loop**。
否则容器刚 `docker run` 起、uvicorn 还没起来,第一次连接撞冷启动 → 失败 → 错误地
落进 fallback(用户看到"醒来中"然后直接 fallback)。等就绪是 infra 等待,不是
agent-loop 上限(铁律 #14)。

## 2026-06-18 — executor OOM（exit code -9）审计可见性

`_record_oom_if_killed(db_client, user_id, error_str, output_already_emitted)`
模块级 helper，在 agent loop 的 `except` 捕获点被调用一次：若错误是
executor 子进程被 OOM-kill（`exit code -9`），best-effort 写一条
`oom_killed` 审计行（`instance_executor_audit`），供监测发现。**告警本身不在
这里做**——NarraNexus 开源，只产生信号（审计行 + `/admin/runtime/status`）；推
Lark 告警由 deploy 仓的 watcher 读这些信号去做（信号/告警分离,开源边界）。
**故意不做重试**——干净重试要求把流式 loop 改成可从头重跑，风险大，留作
后续专项（scheduling-resource plan）；今天 OOM 仍照常落入下方 fallback。
helper 绝不抛错（审计失败只 log），不影响 loop。

## 2026-06-11 — 鉴权失效时跳过 helper fallback（不伪造回复）

agent loop 出现 `ErrorMessage(error_type="auth_expired")`（response_processor
对 codex OAuth 过期等鉴权失败的归类）时，**跳过 `_stream_fallback_recovery`**，
不让 helper 编一个回复把"登录已失效"盖住（incident 2026-06-11：codex
refresh token 已用过 → 每轮静默退化到 gpt-5，用户以为"codex 变笨了"）。
此时 response_processor 已经发了那条 fatal、可操作的 re-login 提示，用户直接
看到它即可。

**坑（已避开）**：不能用 `return` 提前退出——后面还有必须 yield 的
`PathExecutionResult`（Step 4 靠它持久化本轮 Event）。所以是把 fallback 计算
+ `_stream_fallback_recovery` 那段包进 `if not auth_failed: ... else: log`，
auth_failed 时**继续 fall through** 到 sub-step 收尾 + PathExecutionResult。
`auth_failed` 通过扫描 `agent_loop_response` 里是否有
`error_type == AUTH_EXPIRED_ERROR_TYPE`（从 response_processor 导入常量）判定。

## 2026-06-10 — helper obtained via get_helper_sdk()

The fallback-reply stream no longer instantiates OpenAIAgentsSDK directly —
`get_helper_sdk()` (agent_framework/llm/helper_sdk.py) returns the per-task
helper (OpenAI or Anthropic Messages API) based on which helper config the
resolver installed. Call shape (llm_stream) unchanged.

## 2026-05-29 — pluggable driver + EverMemOS removed

The agent loop is now obtained via `get_agent_loop_driver(working_path=...)`
(framework registry, iron rule #9) — do NOT instantiate `ClaudeAgentSDK`
directly here; register a driver instead (see [[loop/driver.py]]).
The former EverMemOS episode await (`ctx.evermemos_task` → `relevant_episodes`
→ `context_runtime.run`) was removed.

## 2026-05-25 — Fatal-path recovery wired end-to-end (`_stream_fallback_recovery`)

The post-agent-loop recovery slot is now a single async generator that:

1. Drains the helper_llm stream as `AgentTextDelta` frames (when mode is `no_reply` or `after_error`).
2. Emits a synthetic `send_message_to_user_directly` `ProgressMessage` carrying `details.reply_via=helper_llm_{mode}` if any content streamed — downstream `chat_module._split_user_visible_response` picks this up like an organic reply, so persistence works without special-casing.
3. Yields the captured `ErrorMessage` LAST with computed severity (`recovered` / `recovered_after_reply` / `fatal`). The frontend reduces synthetic tool calls into `responseParts` first; yielding the error first would briefly flip `displayContent` to the error string before the synthetic lands.

The `except Exception` in the main agent-loop body **no longer yields** the ErrorMessage immediately — it stashes `{error_type, error_message}` into `captured_error` so the recovery generator can place it after the recovered reply. `_generate_fallback_reply_stream` now accepts the full context (system prompts + chat history + agent_loop_response + final_output + error_info) and uses one of two prompt templates (`_FALLBACK_NO_REPLY_INSTRUCTIONS` / `_FALLBACK_AFTER_ERROR_INSTRUCTIONS`); `_build_helper_user_input` assembles the user-input payload via tagged XML-ish sections so the LLM can navigate the context without re-instantiating the agent persona.

Rename: synthetic `details.reply_via` switched from `helper_llm_fallback` to `helper_llm_no_reply` / `helper_llm_after_error` so the UI can distinguish the two recovery modes. `chat_module` now copies any `helper_llm_*` tag onto the persisted row (was strict equality on `helper_llm_fallback`).

Contract is pinned by `tests/agent_runtime/test_fallback_streaming_order.py`.

## 2026-05-25 — Mode-aware fallback decision (`_should_run_helper_llm_fallback`)

Return shape changed from `(bool, str)` to `(mode | None, str)`:

- `"no_reply"` — chat turn ended cleanly without `send_message_to_user_directly`; helper_llm runs to write the missing reply.
- `"after_error"` — chat turn hit a fatal mid-stream and no organic reply was sent yet; helper_llm runs with full context (system prompts + completed tool results + error info) to produce a recovery reply. (Wired in T4.)
- `"partial_reply_then_error"` — fatal hit AFTER an organic reply; helper_llm does NOT run (we already spoke), but the caller surfaces a `recovered_after_reply` ErrorMessage. (Wired in T4.)
- `None` with `skip_reason` — `non_chat_trigger` / `cancellation_requested` / `already_replied_via_tool`.

The decision function is now the single point of truth for "what should this turn do at the recovery slot." Contract is pinned by `tests/agent_runtime/test_helper_llm_fallback_decision.py`.

## 2026-05-25 — Fallback prompt serializer added (`_serialize_agent_loop_for_prompt`)

Pure helper that renders `agent_loop_response` (raw runtime frames) into
a flat ordered plain-text block for the helper_llm fallback prompt. Sits
beside `_should_run_helper_llm_fallback` — both are no-IO, no-async, so
the recovery prompt assembly is unit-testable end-to-end without
spinning up the full async generator.

Per-entry cap defaults to 4 KB, total cap to 32 KB. When total exceeds
the cap, oldest entries drop first (with an `[... earlier activity
omitted ...]` marker) because the recovery reply needs recent activity
more than ancient setup. Adjacent `AgentTextDelta` frames coalesce into
one `[assistant_text]` block so the LLM sees coherent text instead of
the delta soup that's natural for streaming. This is the building block
for the bigger fallback-LLM-context redesign (fatal-path recovery with
full context; design is author-local).

Contract is pinned by `tests/agent_runtime/test_fallback_prompt_assembly.py`.

## 2026-05-13 — Phase B caller migration (generator-based ResponseProcessor)

`ResponseProcessor.process(...)` 在 Phase B 改成 generator。这里的 caller
从 `result = response_processor.process(response, state)` 改成 `for result
in response_processor.process(response, state):`——一个 raw event 可能
产生 0..2 个 ProcessedResponse（thinking 累积时是 0，非 thinking 事件
flush 残余 thinking 时是 2）。

同时在两个出口点（try 末尾 + except 中）调 `flush_pending(state)`——保证
stream 结束 / 异常退出时 batcher 里残留的 thinking 不丢。这是 batcher 设计
明确要求 caller 履行的契约。

## 2026-05-12 — Chat no-reply helper_llm fallback hardening

Self-review of the initial fallback (same-day) caught four real holes;
the fixes are pinned by
`tests/agent_runtime/test_helper_llm_fallback_decision.py`:

1. **Fatal error must skip the fallback**. If `agent_loop_response`
   contains an ErrorMessage with `severity="fatal"` (CLI timeout, SDK
   crash, etc.), `state.final_output` is partial reasoning; asking
   helper_llm to summarise that hallucinates a reply from a half-
   thought. chat_module's failed-turn path handles it instead.
2. **Cancellation must skip — and abort mid-stream**. If the user
   pressed stop, honouring the token is the whole point. The
   pre-check + a mid-loop check on the streaming iteration cover
   both "cancelled before fallback fires" and "cancelled mid-stream".
3. **`state.finalize()` runs before reading `state.final_output`**.
   The previous order read the unfinalized state.
4. **Partial-stream recovery**. If helper_llm errors after some
   deltas have already been yielded, the synthetic ProgressMessage
   is still emitted from `fallback_chunks`, tagged
   `details.fallback_partial=True` + `details.fallback_error`. The
   user keeps the visible deltas and chat_module persists the matching
   partial content — no half-reply + "decided not to respond"
   mismatch in DB.

The skip decision is factored into a pure function
`_should_run_helper_llm_fallback(working_source, agent_loop_response,
cancellation) -> (bool, skip_reason)` so the four guard cases can be
exercised by unit tests without spinning up the full async generator.

## 2026-05-12 — Chat no-reply helper_llm fallback (P0 #3)

After the agent loop completes, step 3 now inspects
`agent_loop_response` for a `send_message_to_user_directly` tool call.
When the turn was chat-triggered (`ctx.working_source == "chat"`) and
no such call exists, step 3 invokes the helper_llm slot via
`OpenAIAgentsSDK.llm_stream` and streams the resulting reply through
`AgentTextDelta` events — exactly the same channel the frontend uses
to render organic LLM stream, so users see the recovered reply in
real time without any frontend change.

After the stream completes, step 3 appends a synthetic
`send_message_to_user_directly` ProgressMessage carrying
`details.reply_via="helper_llm_fallback"`. Downstream:
- `ChatModule._extract_user_visible_response` picks the synthetic call
  up like any organic reply, so the assistant row persists the
  helper-generated text — NOT `io_data.final_output` (reasoning).
- `ChatModule.hook_after_event_execution` lifts the `reply_via` tag
  onto the persisted row's `meta_data.reply_via`.

Why this design (per 5/11 product review):
- `io_data.final_output` is internal reasoning, not speech (project
  iron rule: only `send_message_to_user_directly` counts as speaking).
  The previous "persist final_output directly" shortcut violated this.
- Only chat turns get the fallback. `message_bus` deliberately avoids
  replying to prevent agent-to-agent loops; job/lark/etc. have their
  own reply pathways.
- Streaming the helper_llm output keeps the user experience identical
  to a normal reply (no "blank then long pause then text" UX).

If the helper_llm call itself fails, step 3 logs and lets the
placeholder fall through — the honest record is "no reply" rather
than a silent leak of reasoning.

# step_3_agent_loop.py — Pipeline Step 3 Sub-path: Interactive Agent Loop

## Why It Exists

When `step_3_execute_path.py` routes to the `agent_loop` execution type, this module handles the full sub-pipeline for an interactive LLM-driven turn. It orchestrates sub-steps 3.1 through 3.5: context building, token budget computation, LLM invocation, tool execution, and response processing. This separation keeps the routing layer thin and the agent loop logic focused.

## Upstream / Downstream

**Called by:** `step_3_execute_path.py` — receives `ctx` and yields `ProgressMessage` + `PathExecutionResult`

**Calls:**
- `ContextRuntime.run()` (sub-step 3.2) — builds `ContextData` with all module data injected
- `ClaudeAgentSDK.agent_loop()` (sub-step 3.3) — drives the LLM turn via Claude Code CLI subprocess
- `ResponseProcessor.process()` (sub-step 3.5) — interprets LLM output into `ProcessedResponse`
- `ctx.module_service` — for hook calls between sub-steps

**Produces:** `PathExecutionResult` stored in `ctx.execution_result` by the calling router

## Key Design Decisions

### Sub-step Structure (3.1–3.5)
Each sub-step yields its own `ProgressMessage`. This gives the frontend granular visibility into long-running turns. The sub-step numbers appear in WebSocket progress events, allowing the UI to show "3.3 Calling LLM..." independently.

### skill_env_vars Extraction
`ctx_data.extra_data` is checked for `skill_env_vars` key after ContextRuntime runs. These env vars come from AwarenessModule and are passed directly to the Claude Code CLI subprocess. This is how agent-level tool permissions (e.g., allowed bash commands) propagate to the execution environment.

### Token Budget
Computed before the LLM call from `ctx.event.input_content` length and the loaded context. Budget calculation lives here, not in ContextRuntime, because it depends on the final assembled prompt length.

### Multi-turn History Injection
Chat history is injected into the system prompt (not as native multi-turn messages) because Claude Code CLI's `--system-prompt` flag doesn't support multi-turn natively. The `prompts.py` constants (`CHAT_HISTORY_HEADER`, etc.) wrap the history block.

## ContextData Mutations

| Field | What Happens |
|-------|-------------|
| `ctx_data` | Built fresh by ContextRuntime; not a pre-existing ctx field |
| `ctx.execution_result` | Set by router after this generator yields `PathExecutionResult` |
| `ctx.evermemos_memories` | Read here (cached in step 1); passed to ContextRuntime |

## Gotchas / Edge Cases

- **skill_env_vars missing key**: If AwarenessModule didn't populate `extra_data`, the dict lookup returns `None` gracefully — don't add a default, the SDK handles `None`.
- **ContextRuntime vs agent loop ordering**: ContextRuntime.run() must complete before agent_loop() starts; the context is not streamed incrementally.
- **Sub-step 3.4 (tool execution)**: Tool calls are processed inside `agent_loop()` via MCP — sub-step 3.4 in the progress messages is a checkpoint yield, not a separate function call.
- **ErrorMessage is appended to `agent_loop_response` AND yielded (Bug 8)**: the `except Exception` handler doesn't just push the error to the frontend — it also appends the `ErrorMessage` to `agent_loop_response` before moving on to `state.finalize()` and the `PathExecutionResult` yield. That append is what lets downstream hooks (ChatModule detects it in `hook_after_event_execution` and stores the failed turn with `meta_data.status="failed"` instead of a normal user/assistant pair) see the failure signal. Without the append, hooks see a silently-truncated turn and happily persist it as "success with empty reply", which was exactly the Bug 8 contamination.

## Common New-Developer Mistakes

- Trying to add module data gathering here: all data gathering belongs in `ContextRuntime` (which calls `hook_data_gathering` on each module). This step only orchestrates.
- Assuming `ctx.execution_result` is set inside this generator: the router (`step_3_execute_path.py`) sets it after intercepting the `PathExecutionResult` yield.
- Forgetting that `skill_env_vars` must be a `dict[str, str]` — passing any other type will cause the SDK subprocess to reject it silently.

## 2026-08-12 — `_emit_team_room_delivery`:平台代发也要先确认再记账

team 房间是唯一"你的纯文本**就是**消息"的表面 —— 它的回复面被整体清空,agent 连投递
工具都调不到。于是 turn 的 trace 里没有任何东西说"回复过",`_delivered_to_origin`
**正确地**得出"没回复":每一轮 team turn 都记成 no reply sent,落成 activity 行,而
下一轮的历史加载器会丢掉 activity 行。这就是 team 房间每轮冷启动的成因。

投递因此搬进 turn 里(见 [[message_bus_trigger]] 同日条目):会话行由
`hook_persist_turn` 在 `run()` 返回**之前**写完,trigger 事后再贴,账已经结了。

**但不能乐观地合成伪帧。** 本文件下面 IM DM fallback 那段自己立了规矩:
帧只在**渠道确认发送之后**才发出,因为给一条从未离开进程的消息记"已回复",和我们
正在修的"纯文本被丢弃"是同一类谎。所以 `deliver` 返回 True 才 yield 帧;返回 False
或抛异常都不发 —— 那一轮**确实**没回复。

`deliver` 的语义归调用方(mention 解析、级联封顶、run id 盖章),这里只决定它**算不算**。

帧骑的是 `bus_send_message`:它在 message_bus handler 的 `user_reply_tool_names` 里,
但**不在** `owner_visible_reply_tool_names` 里。这个不对称是承重的 —— 提升它会让每次
团队回复重新锚定 owner 的会话(PR #230 修过一次的 bug),有专门测试钉住。

## 2026-08-13 — `_should_deliver_team_reply`:两个必须挡住的情形

3.4.T 位于 loop 的 `try/except` **之后**,所以"不该投递"的每一个理由都得在那里显式
检查,而且写成内联 `if` 就既容易漏又测不了。抽成命名谓词,和同文件
`_should_run_helper_llm_fallback` 同一个形状。

* **`captured_error`** —— loop 死了。`state.final_output` 此时是断掉之前流出来的部分,
  贴出去就是把一句没写完的话摆在整个房间面前,而它读起来像答案。故障由 trigger 侧
  以房间身份单独通知。
* **`cancelled`** —— owner 点了停止。`CancelledByUser` 只在 step 4 之后才抛(为了让被
  打断的 turn 也进历史),所以这段代码一定会跑到,必须自己查。漏掉的代价不只是多一行:
  投递路径会解析 @mention,于是一轮**被用户杀掉的** turn 能把队友唤醒去跑各自的一整轮。
  本文件两条 helper-LLM fallback 流早就设了同一个门,只有这条 lane 漏了。

## 2026-08-13 (review 后) — 两个判据合一,阶段整体可测

**`_turn_hit_a_fatal` 取代了裸 `captured_error`。** `captured_error` 只在 loop **抛
异常**时被赋值,而 `auth_expired` / `config_actionable` 标着 `severity="fatal"` 却是
**return 一个帧**,不 raise —— 于是门看不见它们,fatal 的一轮照样把断掉之前流出来的
半截明文贴进房间。判定复用 `chat_module._detect_fatal_error_in_agent_loop`,不造第三份
副本:平台不该对"这轮成没成"持有两种意见。

**`_emit_team_room_delivery` 拆成 `_post_team_room_reply`(返回 bool)+
`_team_room_reply_frame`(调用点造帧)。** 原先是 async generator —— 一个没人迭代的
async generator**什么都不做,而且是静默的**,这对"决定一轮会不会被记住"的唯一代码路径
是很差的形状。

**整个阶段抽成 `_team_room_delivery_phase`。** 门和投递各自可测并不够:没有任何测试
覆盖**它们怎么被接在一起**。把门的结果换成 `team_deliver is not None`、或把
`captured_error` 误传 None,这一片的测试**全部保持绿色** —— 本次会话已经栽过同一类
坑三次。现在阶段整体有 6 条测试,并且实测过"把门换成裸判断会让 3 条变红"。

## 2026-08-14 — 一个 fatal 谓词,以及 phase 不再是 generator

**`_has_fatal_error_frame`。** 这个判定此前在本文件里有两份逐字相同的实现(helper-LLM
fallback 的和 team 房间门的),而第三处是**从函数体里 import ChatModule 的私有函数**
—— 一个 runtime step 向上穿透依赖某个模块的内部,来回答一个它自己就能回答的问题。
三份副本就是三个"什么算 fatal"各自漂移的地方。

**`_team_room_delivery_phase` 改成返回 `Optional[frame]`。** async generator 的问题是
**没人迭代它就什么都不做,而且是静默的** —— 对"决定这一轮记不记得住"的唯一路径,这是
最不能接受的失败形状。

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

> **2026-08-20**: `_resolve_agent_framework_name` 的缺行/空列/DB 故障兜底由 `claude_code` 改为 `nexus_power`（平台默认框架变更；仅注释同步，逻辑走 model_identity._DEFAULT_FRAMEWORK）。

> **2026-08-21**: `driver.agent_loop(...)` 新增显式 `steering=ctx.steering`(挨着 `cancellation`)。所有可达 driver(claude/codex/nexus/remote)的 `agent_loop` 都吃 `**kwargs`,故非 nexus driver 安全吸收忽略;只有 NexusAgent 真正消费它(接 SteerChannel)。见 [[nexus_agent.py]] 同日条目。
