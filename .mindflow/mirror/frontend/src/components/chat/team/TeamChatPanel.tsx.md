---
code_file: frontend/src/components/chat/team/TeamChatPanel.tsx
last_verified: 2026-08-14
stub: false
---

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
「正在工作」，那正是四态存在要防止的误读；queued 压到 0.72 不透明度，stalled 走 warning
色调。

**一个 i18n key 都没新增**，直接复用 roster 的
`chat.team.activity.{queued,stalled,waitingFor,silentFor}`。两个界面对同一状态说同一个
词本身就是对的，顺带省掉 10 个 locale 文件的改动。`running` 的 aria-label 逐字保持
`chat.team.typing`——那是这个元素一直以来的可访问名，也是既有测试的抓手。

**测试的坑**：断言时长文案必须 `within(bubble)` 收紧到气泡内部——roster 渲染同一个
字符串，不收紧的查询只靠 roster 就能通过，也就是说它会在「正好是本文件要抓的那个
bug」面前变绿。
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
