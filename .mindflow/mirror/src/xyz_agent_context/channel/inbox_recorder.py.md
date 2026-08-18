---
code_file: src/xyz_agent_context/channel/inbox_recorder.py
last_verified: 2026-08-18
stub: false
---

# inbox_recorder.py — 把一轮对话记进 inbox 自己的表

## 为什么存在

取代 `channel_inbox_writer.py`（同日删除）。旧写入器往 **MessageBus 的表**里写
「五行套装」：伪 agent、频道、**成员行**、inbound、outbound。prod 2026-08-17 实测两笔代价：

- `bus_messages` 里 **86%**（28,605 / 33,164 行）是 IM inbox 内容。bus 自己的表主要装的是
  另一个功能的数据。
- 那个**成员行**的 `last_read_at` **没有任何人推进**（172 个 IM 成员行里 159 个为 NULL，
  92%）。bus 的未读判据是 `created_at > COALESCE(last_read_at, epoch)`，于是 **1,364 条 IM
  历史永久「未读」**，以伪 agent `lark_user_<id>` 的名义灌进 **90 个 agent** 每一轮的上下文
  ——而那个署名不符合任何一条 Source-Recognition 规则。

## 关键设计：containment 是结构性的，不是过滤器

agent 的未读注入读的是 `bus_messages JOIN bus_channel_members`。新行不在那两张表里，
**所以它到不了**——不需要任何前缀判断。

这一点是刻意的：**过滤器正是 `im_channel_prefixes()`，而它漂过**。2026-07-03 wechat 事故
里那个手维护的前缀元组漏了 wechat / narramessenger / discord，每条消息触发第二个 agent
run、穿着 Owner-Relay 的 peer prompt，伪造 context_token、发假的「我已经在微信上回复你啦」。
**能靠结构解决的，不要靠列表。**

## 一个必须知道的例外：这个记录同时是 operational 的

wechat 和 telegram **没有服务端历史 API**，它们的 context builder 从这个记录读对话历史
——所以对这两个渠道，inbox 记录**就是 agent 的对话记忆**，不只是给人看的。
2026-08-17 搬表时这两个读取方跟着一起搬了；留在 `bus_messages` 上会让这两个渠道**失忆**。

spec 里「operational vs observational」那条划分因此是**不完全**的，本条是它的例外。

## 设计决策

- **一轮两行，相差 1 微秒**。共用一个 `now` 会让 `ORDER BY created_at` 在无 tie-break 时
  不稳定排序，回复可能排在它所回答的消息**上面**——微信最严重，它的消息自身没有时间戳
  （`timestamp_ms == 0`）。见 [[test_inbox_ordering]]。
- **沉默的一轮只记 inbound**。真的没说话的轮次不该在用户面板上留一个空气泡。
- **占位名会被真名覆盖**。首次见到的发送者若 `sender_name="Unknown"`，会回退成裸 id；
  不刷新的话，每个新联系人的**第一批消息**会永远显示成一串 id。
- **db 由调用方注入**，本模块从不 import `get_db_client` —— 保持可单测，也让调用方自己
  掌握 handle / 事务选择（沿用旧写入器的约定）。
- **失败会 re-raise**，因为调用方要写自己的 `EVENT_INBOX_WRITE_FAILED` 审计行。
- **空 source 抛错，空 brand 不抛**：source 是身份（决定 thread id、对齐 registry），
  brand 只是展示标签——为了没人给个好看的名字就丢掉一整轮对话，不划算。旧写入器两个都抛。

## thread id 的形状

`im_<channel>_<chat_id>` / `nx_dm_<agent>_<peer>`：**家族前缀在前**，命名空间先说「这是
什么」再说「是哪一个」。peer 线程用**两个 agent id** 而不是只用 peer——同一个 owner 可以
有多个 agent 和同一个 peer 说话，面板是按 agent 列的。

## 不要和它混淆

`InboxRepository` / `inbox_table` / `InboxMessage` 是**另一个功能**——平台推给 owner 的
系统告警。共用了 "inbox" 这个词而已。本模块的表因此叫 `inbox_thread_messages` 而不是
`inbox_messages`，免得读者以为它们相关。更深的命名冲突（真正误用这个词的是通知那一侧）
记在 `reference/self_notebook/todo/`。

## 上下游

- **上游**：`ChannelTriggerBase._process_message`（两处）、`LarkTrigger._process_message`
  与 `_write_to_inbox`。
- **下游**：`inbox_threads` / `inbox_thread_messages`；读取方是 `backend/routes/inbox.py`
  和 wechat / telegram 的 context builder。

## 2026-08-18 — 建会话的竞态：输掉不是错误

`_ensure_thread` 是先查后插，而 `thread_id` 是主键。两轮同时开同一个**新**会话时都读到
None、都插入；输的一方抛异常，`record_turn` 上抛，调用方记 `EVENT_INBOX_WRITE_FAILED` ——
用户面板里少一条消息，而那一轮本身完全成功。debounce 批次与多 agent 群聊都会并发投递，
窗口就是读与插之间的整段间隔。

现在把重复键当作「别人已经建好了」（它本来就是这个意思），刷新走对方那行。**用重读而不是
匹配驱动异常类型**：aiosqlite 与 aiomysql 的重复键异常类不同，写死一种会在另一个后端上
静默停止捕获；行在就是竞态，行不在就是真失败、原异常照抛。与 [[wake_signal.py]] 的
`bump` 同形。

## 2026-08-18 — 沉默不再被写成 agent 自己的发言

`record_turn` 的契约是空 `outbound_text` 不写 outbound 行。托管调用点却传
`... or CHANNEL_SILENT_SENTINEL`，于是沉默变成一条署名 agent 的 `(stayed silent)`；
Telegram/WeChat 把 `inbox_thread_messages` 当会话记忆读回，agent 就看到自己"上一句"说了
这个占位符 —— [[channel_trigger_base.py]] 里 `_platform_reply_text` 的 docstring 亲自
点名过这个失败模式。哨兵值只应活在发送路径的 `already_replied` 比较里。守卫是源码级的：
从记录器内部看，哨兵和真实回复都只是非空文本，这正是行为测试一路全绿的原因。

## 2026-08-18 (二) — `im_thread_id` 必须带 agent

原形 `im_<channel>_<chat_id>` 不含 agent，而 `agent_id` 只是行上的一列、建行时写一次、
之后 `_ensure_thread` 遇到已存在的行就早退、永不更新。面板按那一列筛选，所以第二个 agent 的
消息会追加进第一个 agent 的会话里，**第二个 agent 的收件箱是空的**。

可达而非假设：Telegram 私聊的 `chat_id` 就是**用户**的 id，跨 bot 相同，一个人同时私聊 owner
的两个 agent 就会撞上。被取代的旧写入器给每个 agent 建一行 `bus_channel_members`，两边都看得
见 —— 所以这是回归，不是继承来的缺口。

它同时让 `agent_id` 这一列对任何**按 agent** 划范围的操作可信：否则
[[wipe_service.py]] 的「清空这个 agent 的会话」会删掉另一个 agent 的记录。因此修复顺序是
先这条、再 wipe、再回填（回填必须产出最终 id 形状）。key 与行上的 `agent_id` 列取同一个值，
两者若不同源，面板筛选和会话身份又会脱节。

六个调用点同批更新，含两个**读**侧的 context builder（Telegram / WeChat 按 thread_id 取历史，
不同批改就会让每个 IM agent 丢掉会话上下文）。测试里的 id 一律改为由这个 helper 生成，不再
硬编码字符串 —— 形状改动应该只碰一处定义。
