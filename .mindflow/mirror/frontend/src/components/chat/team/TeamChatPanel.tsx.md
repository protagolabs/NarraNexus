---
code_file: frontend/src/components/chat/team/TeamChatPanel.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — 悬停提示补全

带 aria-label 但无 title 的四处控件(guide「?」开关、composer 错误条与
公告栏的两个关闭钮、房间头部的运行态气泡按钮)补上与 aria-label 同文案的
title——键盘/读屏用户本来就有名字,悬停用户现在也有。

## 2026-08-14 — 转录区认全部「非 idle」状态，不再只认 running

`TypingIndicator` → `LivenessIndicator`，过滤条件从 `status === 'running'` 放宽到
`status !== 'idle'`。

**为什么这是「群里死寂」的真正修复点**：后端一直知情——`queued` 是团队 GET 直接从
待处理消息算出来的，消息落库后**一次 3s 轮询内**就为真，完全不等 trigger；而
`running` 要等轮询间隔 + worker 槽位 + Step 0。所以此前右栏 roster 已经显示「启动中」，
左边的对话流却什么都没有。本地真机复现过这个窗口。

**没有违反两栏布局那条规则**（见 `TeamChatPanel.roster.test.tsx` 的 docstring）：规则
是「**跑完的**轮次不在流里留痕，记录在 roster 里一键可达」，不是「只有 running 才能
显示」。`idle` 仍然什么都不渲染。

**三态的视觉区分是有意的**：只有 `running` 的点会 bounce。queued/stalled 也跳动会读成
「正在工作」，那正是四态存在要防止的误读。

**颜色与文案一律取自 `STATUS_TONES`**（`lib/teamActivity.ts`）。这是第四个渲染同一组状态
的界面，而 `teamActivity.ts` 的存在理由就是「三个界面永远不会对『stalled 长什么样』产生
分歧」。第一版在这里**硬编码**了调色板，结果是同一成员同一时刻：转录区 stalled 画成
warning 琥珀、右栏 roster 画成 **error 红**；queued 则是 silicon 对 warning。两种严重度
暗示，而颜色比文字更先被看到。柔化只允许**叠在语义色之上**（queued 的 0.72 不透明度），
不允许换成另一个语义色。`TeamChatPanel.liveness.test.tsx` 里有测试断言气泡 style 含
`STATUS_TONES.stalled.color`。

**一个 i18n key 都没新增**，直接复用 roster 的
`chat.team.activity.{queued,stalled,waitingFor,silentFor}`。两个界面对同一状态说同一个
词本身就是对的，顺带省掉 10 个 locale 文件的改动。`running` 的 aria-label 逐字保持
`chat.team.typing`——那是这个元素一直以来的可访问名，也是既有测试的抓手。

**测试的坑**：断言时长文案必须 `within(bubble)` 收紧到气泡内部——roster 渲染同一个
字符串，不收紧的查询只靠 roster 就能通过，也就是说它会在「正好是本文件要抓的那个
bug」面前变绿。

## 2026-08-14 (二) — memberNameMap：一个被上游打穿的 memo

`memberNames` 此前是在 JSX 里现场 `Object.fromEntries(...)` 出来的，**每次 render 都是新
对象**。于是三层之外的 memo 全部失效：新对象 → 每个气泡的 `nameSet` 重算 → rehype 插件
数组是新的 → [[Markdown.tsx]] 的浅比较 memo 必然 miss → remark/rehype **重新解析整段正文**。

这个面板**每秒至少 render 一次**（1s ticker 让活动时长走字），打字时每个按键再 render 一
次。200 条消息的房间等于每秒把 200 段 markdown 重新 parse 一遍。

**这是上一轮修复自己引入的**：@高亮从"改写字符串"搬到"插件数组"之前，`Markdown` 的
`content` 是按**值**比较的，memo 命中；搬完之后命中条件变成了**引用相等**，而没有人让那
个引用稳定下来。三处注释（气泡里、Markdown 的 prop 文档、mirror md）都写着"必须传稳定的
数组"，只是上游没兑现。

现在 map 只算一次，两个调用点共用。`TeamChatPanel.renderCost.test.tsx` 直接钉住**身份**
（`toBe`），而不是去数解析次数——后者要伸手进 ReactMarkdown 内部。

## 2026-08-14 — transcript 外面那层 `space-y-5` 不是冗余的

review 读成了"只包着一个子元素"，实际它包着 transcript + 正在输入指示器 + 滚动锚点。
[[TeamTranscript.tsx]] 自己那层 `space-y-5` 间隔的是**消息之间**，外面这层间隔的是
**transcript 和它下面那些东西之间**。换成 Fragment 会把这个间距关掉。留了注释说明。

## 2026-08-14 — 往上翻历史，以及一个跨房间的游标 bug

房间现在打开在**最新**一页（服务端改动见 [[teams.py]]），滚到顶部请求上一页
（`before` 游标，见 [[mergeTeamMessages.ts]] 的 `beforeCursor`）。

三件容易写错的事：

- **滚动位置要自己还原。** 往前面插内容会把读者正在看的一切**下移**恰好等于新增内容的
  高度。不管 `scrollTop` 就等于把人从"让他往上翻的那条消息"旁边瞬移走——这恰恰是"加载
  更多"唯一不能做的事。
- **空页要锁住。** 到顶之后不锁，房间会在之后每一个 scroll 事件上重新问一次。
- **换房间要忘掉这个锁**，否则第二个房间会默默拒绝翻历史。

等待也要看得见：滚到顶部却什么都没发生，和"已经到房间开头了"长得一模一样。

顺带修掉一个真 bug：`refresh` 通过 ref 读 transcript，而换房间那一刻这个 ref 里还是
**上一个房间**的消息——于是新房间是带着另一段对话的 `since` 去取的。新房间里早于那个
时间戳的消息永远不会到达；如果上一个房间的最后一条更新，新房间直接渲染成空的。现在在
清空 `messages` 的同一处也清 ref。

## 2026-08-14 — 输入框补齐三件事

**草稿。** 此前是一个裸 `useState`：换房间或导航走，写了一半的内容直接消失。现在按房间
存（[[chatDrafts.ts]] 的 `getTeamDraft` / `setTeamDraft`），防抖 400ms + 切换/卸载时
立即 flush。

这里比私聊那边麻烦一点：私聊靠 `key={agentId}` 让 `<Composer>` 重挂载，房间**不重挂载**
——路由参数变了，组件还是同一个实例，`teamId` 和 `text` 在**不同的 commit** 更新。所以
面板持有 `draftRoomRef`：这段文字属于哪个房间。少了它，切房间后的第一次保存会把上一个
房间的话记在新房间名下，用户在错误的地方找到自己写的东西。

**IME。** 房间的 textarea 之前**没有输入法保护**：按 Enter 选拼音/假名候选词，直接把
消息发出去了。对这个项目实际使用的语言来说，这不是边缘情况，是输入框对一整类输入是坏的。
标志位不够——它在一个宏任务里被清掉，而某些输入法**先**发 compositionend **再**发那次
keydown，所以还需要 100ms 的宽限窗口。两者都是私聊 [[Composer.tsx]] 早就踩出来的。

**失败要看得见。** 发送失败原本静默地把文字放回去——这和"回车没生效"无法区分，用户于是
重打一遍，或者发两次。上传失败原本注释写着"静默，用户可以重试"，这句话假设用户知道有
东西需要重试；而 `success: false` 的拒绝连异常都不是，表现为"没有出现附件条"，看起来
就像还在上传。现在都走 `composer-error` 那一行，下一次尝试开始时清掉——留在成功消息旁边
的陈旧报错本身就是一句假话。

录音上传失败是其中最糟的一种：录音**没法重打**。

## 2026-08-14 — 打开的房间自己标记已读

每次消息变化都把水位线推到屏幕上最新的一条（[[unread.ts]] 的 `markTeamRead`）。
只在挂载时标记一次的话，用户坐在那里读的正是把行标记出来的那些消息，而行还标着。

**这里的规则和服务端相反，且是有意的**：服务端决定「这值不值得一个标记」（排除
用户自己的消息和平台通知），面板记录「用户看到了什么」——渲染在他面前的一行，
不管是谁写的，都已经被看到了。标记少于屏幕上显示的内容，会让一个只是自己念叨了
两句的房间永远挂着标记，而用户没有任何办法清掉它。

两个 surface 推同一个水位线（另一个是 [[AgentList]]，它只看得到列表响应）。两边
都是单调的，所以谁更靠前谁生效，谁也无法把对方推回去。
## 2026-08-10 — 巡查行渲染

`msg_type === 'patrol'` 走独立分支:虚线框 + 「Leader 巡查」小标 + Markdown 正文。

**不能当普通气泡渲染**,两个原因:巡查是以房间自己的标记(`team_<id>`)落墙的,
`author_name` 会解析成裸 id;而且画成气泡会让 Leader 看起来在不停打断房间,
而它其实是平台在盘点。

## 2026-08-07 — 停止留痕渲染成系统行

`msg_type === 'system_stop'` 的消息走独立分支:居中的小胶囊,文案
`chat.team.stoppedNotice`(带 agent 名)。**不复用气泡**——把它画成 agent
自己的回复,读起来就像 agent 在宣告自己的死亡;这条消息是房间在说话。

团队任务是当众跑的,所以也应当众停止:没有留痕,其他成员只看到一个凭空
消失的任务,只能猜是跑完了、崩了、还是还在跑。

## 2026-08-07 — 右侧挂上团队工作台

根布局从纵向 flex 改为「横向 flex：transcript 列 + [[TeamWorkspacePanel.tsx]]」。此前该组件
注释里的「artifacts 暂不提供」不再成立。

## 2026-08-11 — composer 底色对齐单聊(card 白 + hairline)

团队 composer 此前落在 ui/Textarea 默认的 paper-warm 填充上,与单聊
Composer.tsx 的 card 白底不一致——Owner 双截图对照抓出,并由此在
design_system.md 补了 §2.5 表面层级("同一功能件跨页面同层"/"输入面与
容器差一层")。现补上与单聊相同的 `bg-[--nm-card]` 覆盖;发送键 rest 态
warm 填充在白底输入框上可见,与单聊完全一致,零改动。


## 2026-07-31 — roster v2 接线：accent 下传 + drawer 不再定宽

TeamRosterPanel 拿到 `accent`（团队色）画选中态；移动端 drawer 的
className 去掉 w-64 —— 宽度归 roster 自己（256↔430px 呼吸），定宽会
把展开变宽顶掉（cn 是 tailwind-merge，后写的宽度赢）。

## 2026-07-31 — 每条 agent 回复挂自己的 reasoning 展开条

气泡内 `BusAttachmentList` 之后渲染 [[TeamMessageProcess]]（仅
`!is_user && m.event_id`）——单聊 MessageBubble "View reasoning & tools"
的团队版。数据链：trigger 发帖时把 turn 的 event_id 写进
`bus_messages.event_id` → chat GET 序列化 → 气泡按需 `getEventLog`。
历史消息 event_id 为 null，自然不显示按钮（无死按钮）。分工：**消息气泡
负责历史（每条 turn 各自可开）**，roster 详情只负责 live/最近一轮。

## 2026-07-30 — guide 横幅退役：空房 hero + member bar 的 `?` popover

`TeamRoomGuide` 那条常驻灰字横幅整个删掉（文件已删），寻址规则改由
[[TeamRoomHero]] 提供的两个出口承载：空房时 `messages.length === 0` 分支渲染
hero（替掉原来的 `Users2 + chat.team.empty` 块，`chat.team.empty|emptyHint|
emptyHintWithLead` 三个 key 就此在代码里无人读），有消息之后靠 member bar 里
`?` 按钮弹出的 `GuideRuleCards` popover。横幅是「提示债」——为一个用户只需要
知道两次的事实永久占掉一条房间宽度，还得靠 localStorage 记折叠状态才勉强能忍。
新方案两处都是按需出现，`nx.team.guide.*` 记忆随之消失，无需迁移。

`?` 容器接管了原先 Settings2 身上的 `ml-auto`（Settings2 去掉，否则两个
`ml-auto` 会把设置钮推到自己一组里）。popover 靠 `guideRef` + document 上的
`mousedown` 监听外点关闭，且只在 `guideOpen` 为真时挂监听——常挂会让每次点击
都过一遍这段判断。

## 2026-07-30 — 两栏布局：roster 常驻，console/气泡退役

Timeline+Composer 外包一层两栏容器，右侧挂常驻 [[TeamRosterPanel]]（桌面
恒显，窄屏由 member bar 的 Users2 钮开 overlay drawer）。据此退役两个旧
表面：顶部折叠 console（TeamActivityConsole 文件已删）和消息流底部的
TeamActivityBubble——后者的 idle-trace 保留期正是「跑完后气泡赖着不走」
的根源。消息流只剩本文件内的 TypingIndicator：仅 running 成员渲染、无
统计、回复落地即消失；点击它展开 roster 里该成员的详情（expandedId 状态
由本组件持有，两侧共享同一高亮，这就是 roster 用受控 props 的原因）。


## 2026-07-28 — moved into `chat/team/`, activity + guidance split out

The surface reached three files, so it became a package (铁律 #23):
[[TeamActivityConsole]] (status console + transcript bubble) and
`TeamRoomGuide` (addressing help, retired 2026-07-30) moved out of this file; the panel now
composes them. `@/components/chat/TeamChatPanel` → `@/components/chat/team`.

What the panel itself kept:

- **One clock.** A single `now` (1s ticker, epoch ms) is passed to every child
  so no two durations on screen disagree by a tick. The ticker now runs while
  ANY member is non-idle, not only `running` — the `queued` "waiting Nm" and
  `stalled` "silent Nm" counters are the whole point of those states.
- **`lead_agent_id`** from the response drives both the guide's "who answers"
  line and a dot on that member's avatar in the roster.
- `thinking` is gone; the empty state and the bubble list derive from
  `activity` (see [[teams]]).

The old inline status strip and the dumb "…" queued bubble are replaced by the
console and [[TeamActivityConsole]]'s bubble respectively.


## 2026-07-21 — voice input (mic), parity with single-agent chat

Tools row gained an `AudioRecorder` (mic) next to the attach `+`. Records → uploads with
`source:'recording'` → backend Whisper → the memo joins `pending` and renders as a
`VoiceTranscript` chip; on send it flows like any bus attachment (agents get the transcript
in their marker). Reuses the ChatPanel plumbing: a mount `getTranscriptionAvailability`
probe, a click-time `onPreflight` re-check, a "voice unavailable" `<Dialog>` (→ Settings),
and a post-record notice banner. New i18n keys `chat.team.transcriptionUnavailable|
voiceUnavailableTitle|voiceUnavailableBody|voiceUnavailableProbeFailed|openSettings|cancel`.

## 2026-07-21 — user can attach files in the composer

Composer gained a paperclip button + hidden multi `<input type=file>`. Picked files upload
immediately via `api.uploadTeamChatAttachment` into a `pending: BusAttachment[]` state,
shown as removable chips above the textarea; `handleSend` passes `pending` to
`api.sendTeamChat` and clears it (restores on failure). An attachment-only message (no text)
is allowed. New i18n keys `chat.team.attach|uploading|removeAttachment` (en+zh).

## 2026-07-20 — bus attachments render in the room

Each message bubble now renders `<BusAttachmentList attachments={m.attachments} />`
below the text, so files an agent sent/shared into the team room show as
chips/thumbnails. `TeamChatMessage` gained `attachments?: BusAttachment[]`
(populated by `GET /api/teams/{id}/chat/messages`). See [[BusAttachmentList]].

# chat/team/TeamChatPanel.tsx — Team group-chat surface

## Why it exists

The user-facing view of the homepage's "agent team": one team's shared room,
rendered in the SAME main slot as the single-agent [[ChatPanel]] so switching
between an agent and a team is seamless (see [[MainLayout]]'s `TeamChatView`).

## How it works

Messages flow over the **message bus**, NOT a single-agent narrative:
- **Send** → `api.sendTeamChat(teamId, content, mentions)` → `POST
  /api/teams/{id}/chat/messages`. The composer text's `@tokens` are resolved to
  member `agent_id`s (or the literal `"@all"`); the backend posts as the
  synthetic sender `usr_<user_id>` and maps `@all` → bus `"@everyone"`.
- **Transcript** → polls `GET /api/teams/{id}/chat/messages` every `POLL_MS`
  (3s). The response also carries `thinking: string[]` — members the bus trigger
  is currently processing — which drives the "…" typing bubbles.

## Design decisions / gotchas

- **@-mention autocomplete** with an `@all` option pinned on top; keyboard
  (↑↓/Enter/Tab/Esc) + click. `@all` is a `MentionOption` kind, not a member.
- Bubbles mirror the single-agent [[MessageBubble]]: carbon-soft (user, right,
  carbon right-edge) vs silicon-soft (agent, left, silicon left-edge), meta row
  outside the bubble. Agent content is rendered through `<Markdown>` (the replies
  are markdown); user content stays plain text. `.content.trim()` + the global
  `.markdown-content > :first-child/:last-child { margin: 0 }` rule keep the
  agent bubble's vertical padding equal to the user bubble's.
- The room itself is created/owned by the backend (`created_by = team_<id>`);
  this panel never touches the bus directly — it only calls the two team-chat
  routes. Agent replies (and agent→agent @ cascades) are produced server-side by
  the MessageBusTrigger and just appear in the polled transcript.

## 2026-07-22 — team activity visualization

Consumes the new `activity` from `getTeamChat` ([[teams]]). Renders a top **status strip**
(chip per running/queued member: dot + name + phase·elapsed) and, at the bottom of the
timeline, an **activity bubble** per active member — running shows a spinner + live phase
(思考中 / 调用 <tool> / 回复中) + elapsed; queued shows the "…" dots. A 1s ticker advances
elapsed between the 3s polls. Replaces the old dumb `thinking` "…" bubbles. i18n
`chat.team.activity.*`.

## 2026-08-10 — the workspace loader has a second trigger

It keys on `messages.length`, on the reasoning that a turn which registered
something has just landed in the transcript — cheap and honest for the case it
was written for. It misses exactly one: a wipe of the team's FILES, which
empties the panel while leaving the transcript unchanged, so the panel went on
listing rows the server had deleted and every one of them 410'd on click.
`workspaceRefreshTick` from [[chatStore.ts]] covers it.

## 2026-08-11 — 公告栏：状态、入口、系统消息

公告栏的数据和重载持有在这里（和工作台同一个理由：变更会在 transcript 里落系统行，
两个面必须对「什么时候变了」有共识）。每次写入后**同时**重载公告栏和 transcript，
而不是等下一个轮询周期才让 transcript 追上。

入口放在房间标题栏，是**常驻 chrome，不藏在设置里**——公告栏回答的是
「这个团队已经知道什么」，那是边读房间边问的问题。按钮带条目数，
让已存在的公告栏**自己宣告存在**，而不是等着被发现。

`system_bulletin` 照 `system_stop` 的先例渲染成居中灰行：公告栏变更是**房间在说话**，
不是某个成员在说话；套成成员消息就把平台事件归给了恰好触发它的那个人。

面板加载 effect 也挂在 `workspaceRefreshTick` 上：清团队数据可能带走公告栏，
面板留在屏上列已删掉的规则比空着更糟。

## 2026-08-13 — 投递通知渲染

`system_undelivered` / `system_delivery_failed` 两个新平台行，照 `system_stop` /
`system_bulletin` 的先例渲染成居中胶囊行——平台在说话，不是成员在说话。

两种行**视觉权重不同**：上墙失败是**我们的错、可追责**，用 warning 色；静默一轮只是
信息，和别的平台行一样安静。失败原因不进正文，落在 `title` 上（hover 才见）——
transcript 保持安静，排障的人一个 hover 就能拿到原因。

`content` 是给纯文本消费者的英文兜底，**不是**给读者看的：数据库不知道读者的语言，
所以正文照 `stoppedNotice` 的路子走 i18n key。测试里专门钉了「英文兜底句子不上屏」。

对应后端：[[delivery_notice]]、[[message_bus_trigger]]。

## 2026-08-12 — 消息渲染抽件（1134 → 970 行）

抽出 [[TeamMessageBubble]] / [[TeamTranscript]] / [[TeamSystemLine]] / [[TeamMessageFooter]]。
**只抽本批要动的**——整体重排作为独立 PR 更好 review，混进行为改动里更难。

被抽走的三块（系统行、footer、时间戳）markup 与理由**原样搬运**，本批不改它们的行为。

## 2026-08-13 — 增量轮询与滚动礼让

轮询改为携带 `since`（见 [[mergeTeamMessages]]）。**首次加载仍然整体替换**：
切换房间时往陈旧数组里合并，会把上一个团队的消息显示在新团队名下。

transcript 用 `messagesRef` 而不是把 `messages` 放进 `refresh` 的依赖：
依赖变化会**每来一条消息就重建一次定时器**，那正是把 3 秒轮询变成快得多的轮询的方式。

滚动只在读者已经贴底时跟随（见 [[scrollStickiness]]）。
