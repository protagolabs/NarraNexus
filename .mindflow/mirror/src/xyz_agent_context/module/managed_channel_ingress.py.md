---
code_file: src/xyz_agent_context/module/managed_channel_ingress.py
last_verified: 2026-08-26
stub: false
---

## 2026-08-26（下午）— 这里盖的是**字典**，模型看不到

⚠️ **本文件的 gotcha，从这一侧进来的人必须先知道**：这里的盖章只写
`trigger_extra_data["channel_tag"]` 这个**字典**。而模型读的是
`build_inbound_run_context` 早就渲染好的那个字符串——它在这些 hooks
**之前**就定型了。

让信号真正到达模型的是 [[openai_compat.py]] 在 hooks **之后**调的
[[sync.py]] `retag_managed_input`（用已盖章的字典重建那一行）。
**两者缺一，这个信号就到不了模型。**

这不是假想：上一轮 review 抓到的正是这个 Critical——盖章在，但发生在渲染
之后，于是托管 A2A DM 的 tag 与人类对话逐字节相同，而 DM 协议里那条
「若标记为 `agent sender`」成了模型永远走不到的分支。而
`MatrixTrigger.is_agent_peer` 是全仓唯一答得出这个问题的实现，
NarraMessenger 又恰好跑在托管模式下——受影响面正是这个信号唯一有用的地方。

所以**删掉或挪动 `retag_managed_input` 会原地复现那个 Critical**。
`test_agent_peer_signal.py` 里那条端到端断言（真的调 `before_run`，不是手抄
盖章逻辑）是这条链的回归防线。

## 2026-08-26 — 托管路径自己盖 `is_agent_peer`

原生路径在 `build_trigger_extra_data` 里填，托管路径**不跑 context
builder**，所以那条填充够不到它。不盖的话，每一个托管 A2A DM 对下游都读作
人类对话。

用 try/except 包住，与 `_stamp_turn_envelope` 包住 `managed_reply_kwargs`
同一个理由：一个坏到答不出「对面是不是机器」的 trigger，代价应该是这一轮
少一个信号，不是整个渠道塌掉。

**只在为真时写键**（不是「降级为 False」——代码里现在没有任何一处写 `False`，
except 分支什么都不写）。与 `ChannelTag.to_dict` 丢假值同一条规则，所以托管
turn 与原生 turn 持久化的 tag 键集合一致；键缺失在下游本来就读作 False。

## 2026-08-10 — 托管入站生命周期落审计(batch-2 §B;review 后直写重构)

coordinator **直接持 `ChannelTriggerAuditRepository` 写**(`_audit`
never-raise 助手),不经 trigger seam——两条 deny 路径(trigger 加载
不上、gate 崩溃 fail-closed)恰恰在 trigger 不可用时触发,"依赖坏件
才能记坏件"的机制记不了。**全部 deny 路径落 `managed_ingress_denied`**
(含基础设施两条:整渠道级故障若无痕会被二分法误判成"平台没发",
方向正好指反);silent → `managed_ingress_silent`;convert_attachments
→ `managed_attachments_converted`(declared vs converted;**workspace
解析失败的 early-return 也落行**,error="workspace_resolution: …"——
那是"全部附件丢失"、最需要覆盖的一格)。`_wire_message_id` 统一
message 身份推导(ParsedMessage 与审计行共用,不漂移);审计行带
chat_id/sender_id(取自 channel_tag)。after_run 增加 `audit_details`
透传。设计取舍:复用 `channel_trigger_audit` 表而非新建(todo §B
原案)——现表已有 JSON details/保留期/查询助手,平行审计系统违背
铁律 #8。

## 2026-08-10 — before_run 补 #254 turn envelope

`_stamp_turn_envelope`:native turn 的信封由 context builder 产出
(经 `build_trigger_extra_data`),managed turn 没有 builder ——
不补的话 step_3 把每个 managed 1:1 DM 读成 group room,无回复兜底
在整个托管面是死代码(与 #254 修的原生缺陷同类)。room_type 从
wire `chat_type` 推(仅 "group" 算群;DM 是 "private" 或缺省);
reply kwargs 走 trigger 的 `managed_reply_kwargs` seam,无 trigger
→ 不写(step_3 按 room_id 单独投递)。best-effort,永不破 turn。

## 2026-08-04 — silent_ingest 只调缝(review)

不再触碰 trigger 私有方法;契约知识(extra 字典 → Attachment 对象)留
协调器,编排知识归 trigger 的 `managed_silent_ingest`。
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

## 2026-08-03(补2) — `silent_ingest`:非 @ 群消息的记忆摄取

`is_mention=false` + `chat_type=group` 的托管 turn 不跑回复 run(会闯进
群闲聊),改调 trigger 的原生
`_build_and_run_agent_silent_batch`(单条成批):narrative 路由 + 记忆
写入照跑、LLM step 跳过、绝不向房间发任何东西;openai_compat 以回执
completion 应答。never-raise,失败降级为 dropped 回执。合批(平台侧
collect / 我方批量语义)按 spec Q8 留待实现期与平台共定——单条成批
先保证语义正确。

## 2026-08-03(补) — `convert_attachments`:平台落盘附件并轨原生协议

平台 ingest 已把附件写进 workspace(`chat-attachments/...`)并在契约里
传 `{name,mime,size,path}`;但原生 marker 经 upload store 的 per-day
索引解析路径,平台落点不在索引里 → 必须把每个文件**重新经
`persist_attachment_bytes` 入库**(= 原生 fetch_attachments,"下载"换
成本地读),拿到 file_id/marker/STT。转换后写
`extra_data["attachments"]`,原始键必被消费(未转换的平台字典绝不能
流进 marker 管线)。全程 never-raise:坏 ref 单文件降级 text-only。
路径逃逸守卫与 files.py 的 `_safe_resolve` 同语义。

## 关键决策 / Gotcha

- **失败语义按钩子性质分叉**:副作用渠道 fail-open(构造失败/钩子异常
  → 放行,下游自然暴露 no_credential),narramessenger fail-closed
  (它的钩子就是授权门;类缺失/异常 → deny)。
- `synthesize_managed_message` 只重建业务钩子需要的最小 ParsedMessage;
  wechat 回复路由读 `raw["context_token"]`(wire 上是 reply_token)。
- 单例 + per-channel 实例缓存;构造失败按渠道隔离(与
  run_channel_triggers 同款防御姿态)。
