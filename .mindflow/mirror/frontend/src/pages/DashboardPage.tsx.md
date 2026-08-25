---
code_file: frontend/src/pages/DashboardPage.tsx
last_verified: 2026-08-25
stub: false
---

## 2026-08-25 — Team 头像改为复用 Messenger 的 GroupAvatar；溢出气泡去掉圆环

上一条把 Team 名称左侧的色点换成了"自定义色描边圆环"，Owner 反馈要求改成和
[[../../frontend/src/components/layout/MessengerSection.tsx]] 的
`TeamMessengerRow` 完全一样的头像——固定 `carbon → silicon` 双色
`GroupAvatar`（`size="sm"`，中心放 `teamAvatarInitials` 生成的缩写），不再用
`tm.team.color` 描边。三个位置（这里 / Messenger 侧栏 / `AgentTeamAvatars.tsx`
的 Team hover trigger）现在渲染同一种 Team 头像；`tm.team.color` 这个自定义色
字段本身没有删，`TeamManagementModal` / `TeamDetailPage.tsx` 继续用它，只是不
再出现在这一格。testid 不变：`team-avatar-${team_id}`，里面包一层
`[data-nm="group-avatar"]`。

同时 `TeamMemberAvatars.tsx` 的 `+N` 溢出徽标去掉了 `RingAvatar` 圆环——Owner
要求纯文字，不要头像化——改成一段带左侧留白的小号灰字 `+N`，其余行为（悬浮显示
成员总数 Tooltip）不变。

## 2026-08-25 — Teams 表三处头像细化：Team 圈头像、Members 收紧到 2、Creator 用 carbon

在上一条改动基础上的三处小调整：

1. **Team 名称左侧的纯色小圆点**（`tm.team.color` 填色）曾短暂换成一个 24px
   的自定义色描边圆环——**此做法已被下一条记录取代**，改用共享的 `GroupAvatar`
   （见上）。
2. **Members 列的可见头像数从 3 收紧到 2**（`TeamMemberAvatars` 的 `max`
   prop），超出部分继续折叠进 `+N` 气泡。纯参数调整，组件本身默认值
   (`max = 4`) 不变，只有这个调用点传 `2`。
3. **Created by 列的 `RingAvatar` 从 `species="silicon"` 改成
   `species="carbon"`**——团队创建者是人类，`carbon` 正是 NM 设计系统里
   "human/AI identity" 的人类色（Axiom #1，见
   [[../../frontend/src/components/nm/identity.tsx]] 的 `speciesColor`），
   `silicon` 才是 Agent 用色。Leader 列的 `RingAvatar` 保持 `silicon`
   不变——负责人本身就是一个 Agent。

## 2026-08-25 — Teams 表 Members 列改为 Agent 头像 + 悬浮 Profile；移除 Manage 按钮

Teams 表的 Members 列不再是"N 个 Agent"纯文字，改用新组件
[[../../frontend/src/components/agents/TeamMemberAvatars.tsx]] 渲染每个成员各自
的头像（`-space-x-2` 轻微重叠，`max=3` 后折叠为 `+N` 溢出气泡）。每个头像 hover/
focus 弹出该 Agent 的迷你 Profile 卡（名称、私有/公开图标、状态点、简介、
Runtime/Model/Owner 三行），点击直接 `navigate('/app/agents/:id', { state: {
from: 'dashboard' } })`——与 Agents 表整行点开 Profile 的目的地完全一致，只是入口
从"整行"收窄成"单个头像"。字段解析走新增的 `agentsById`(`rosterAgents` 建的
Map，与既有 Leader 列解析方式一致)+ 既有 `statusById`；不在 viewer 自己 roster
里的成员（比如别人拥有的共享 Agent）只显示 id 派生的初始头像，Runtime/Model 留
空——这是既有 Leader 列同样的已知限制，不是本次新引入的缺口。

Created by 列同一批改成头像 + 姓名，复用 Leader 列已有的 `RingAvatar` + 姓名写法
（视觉统一）；解析方式不变，仍是 `displayName || userId`(团队本身没有 owner
字段，只能显示当前用户)。

Action 列的"管理"按钮（`Users2` 图标 + 文字，开 `TeamManagementModal`）整个删除，
只保留原有的聊天图标按钮——Owner 判断这颗按钮和旁边的聊天图标功能重复，保留一个
更清楚的入口即可。`TeamManagementModal` 挂载连同 `teamsMgmtOpen` state 一并从本
页删除（组件本身仍在，`TeamDetailPage.tsx` 继续用它，没有变成死代码）。Members/
Created by 列变宽，Action 列相应收窄，grid 模板从 `1fr_140px_90px_140px_120px_180px`
调整为 `1fr_140px_132px_140px_100px_72px`。

`formatFramework` 在 `TeamMemberAvatars.tsx` 里又拷贝了一份——延续本页与
`AgentProfilePage.tsx` 已有的"故意不共享，保持两处独立演进"的 Owner 裁决（见
`AgentProfilePage.tsx` 里 `agentChatButtonClass` 旁的注释），没有借这次改动去做
跨文件抽取。

## 2026-08-25 — Profile 导航带上来源标记

Agent 行的两处 `navigate(\`/app/agents/${agentId}\`)`(点击行 + Enter/Space）
现在都带 `state: { from: 'dashboard' }`。这是给
[[../../frontend/src/pages/AgentProfilePage.tsx]] 的 breadcrumb 用的——Profile
页现在有两个入口(这里 + `ChatHeader.tsx` 的头像),需要区分“返回”该回
Dashboard 还是回 Chat。这里的改动只是打标记,不影响本页任何行为。

## 2026-08-24 — 移除 Agent 行三点菜单

Agent 行只保留醒目的 Chat/对话按钮,不再挂载 `AgentRowMenu` 或行内 rename、编辑、
清数据、删除、公开切换的状态与 Dialog。Agent 的常规配置统一进入 Profile 的
Settings,避免 Dashboard 同时承担目录、详情和配置职责。移除 kebab 后 Action 列
从 104px 收紧为 80px；Team 表自己的管理菜单不受影响。

## 2026-08-24 — Agent 对话入口强化为文字按钮

Owned 与 Public Agent 行右侧不再使用难以识别的孤立 MessageSquare 图标，而是
统一显示带轻描边、surface、阴影和 hover 状态的“图标 + Chat/对话”按钮。按钮
继续使用 `openChat` 作为无障碍名称，并阻止事件冒泡，因此不会触发行级 Profile
导航。Action 列从 64px 放宽到 104px，以容纳本地化文字和 owned 行的 kebab 菜单；
Team 表的聊天动作不在此次 Agent 目录调整范围内。

## 2026-08-24 — Agent 行改为 Profile 导航

Agent 目录不再承载行内展开详情。整行现在是可点击、可通过 Enter/Space 打开的
Profile 链接,目标为 `/app/agents/:agentId`;聊天与 kebab 行内操作继续阻止事件
冒泡,不会误跳 Profile。原先的 chevron、expanded Set、动画容器以及
Attention/Queue/Metrics/Sessions/Jobs/Sparkline/RecentFeed 挂载全部移除,这些内容
不再挤压目录表。Public Agent 仍保持原先的只读目录行与聊天入口,不打开需要
owner-scoped 能力数据的 Profile。

## 2026-08-24 — 新增 Channels 列

Agents 表在 Teams 与 Framework 之间新增 Channels 列,读取
`AgentInfo.bound_channels` 并只显示已绑定渠道的 16px 真彩品牌图标;hover/focus
通过 Tooltip 显示完整渠道名。覆盖 Lark/Feishu、Slack、Telegram、WeChat、
NarraMessenger、Discord、Home Assistant,未知渠道回落通用 Bot 且保留原始名。
无绑定显示 `—`。

为容纳新列,Agent 表统一改为 8 列 grid,适度收紧 Name/Status/Teams/Last active,
Model 继续占弹性空间;owned 与 public 行使用同一模板。数据随现有 agents 列表
一次返回,页面不发 per-agent/per-channel 请求。

## 2026-08-24 — Teams 列改为头像 + 悬浮 Profile

Agent 行的 Teams 列通过共享 `AgentTeamAvatars` 渲染,不再使用带文字的彩色
chips;已加入的每个 Team 只显示一个 32px 标准 NM `GroupAvatar`,中心用团队名称
生成 1–2 字符简称。固定传入
`carbon → silicon` 两段,利用组件从顶部顺时针绘制的顺序形成参考 UI 中左侧
silicon 蓝、右侧 carbon 橙的双色圆环。多个团队用 `-space-x-2` 略微重叠,
hover/focus 的头像提升 z-index 并轻微上移;未加入团队的 Agent 仍显示弱化的
`—`,与 Channels 等空值列保持一致。

每个头像是可聚焦的 Tooltip trigger,hover 或键盘 focus 后通过 portal 展示小型
Team Profile:40px 同款 `GroupAvatar`、完整名称、成员数量,以及 `description` →
`intro_md` → 本地化空状态的介绍降级链。portal 避免被目录容器或 ScrollArea
裁切;点击头像会停止行事件冒泡,不会意外展开 Agent。所有内容来自已加载的
`teamsStore.teams`,不新增请求。Agent Profile 复用同一组件,两处视觉与交互不再
各自演进。

## 2026-08-24 — 移除 Agent 表框线

Agent 目录不再使用四周 border 和圆角容器,同时移除表头底线、各 Agent 行的
横向分隔线以及展开详情内部的分隔线,表格内容直接铺在页面 surface 上。Team
chip 自身的描边继续保留,因为它表达标签边界而不是表格网格。该变化仅作用于
Agents 表;数据、列宽、展开逻辑和行内操作均不变。

## 2026-08-24 — Framework / Model 品牌图标

Agent 表的 Framework 与 Model 值前增加对应品牌图标。Framework 使用
Claude Code → Claude、Codex CLI → OpenAI、Nexus Power → NarraNexus 自有
logo 的映射;未知但有值的 framework 使用通用 Bot。Model 复用
[[../lib/modelBrandIcons.ts]] 的 model-id 厂商识别,覆盖 Claude、OpenAI、
Gemini、DeepSeek、Kimi、Qwen、MiniMax、GLM,无法识别时同样回落 Bot。

图标是装饰信息并用 `aria-hidden` 包裹,文字仍是可访问名称;缺失配置显示 `—`
且不渲染无意义图标。OpenAI 的品牌 SVG 是固定黑色,深色主题单独使用 invert
保证可见,其余品牌保持现有真彩。图标只消费列表已有字段,不增加接口请求。

## 2026-08-24 — Agent 表统一字体系统

Agent 目录表统一继承产品 sans-serif 字体,表头、Agent ID、团队标签和模型名
不再各自切换为等宽字体。信息层级仍由字号、字重和颜色表达:名称保持主信息
字重,ID 与 Last active 使用弱化色,团队 chip 使用同字体的 medium 字重。此次
只处理字体协调性,不改变表格布局、列宽、数据或交互。

## 2026-08-24 — 增加 Last active 列,复用现有状态轮询

Agent 表新增 Last active,直接读取本页已经轮询的
`GET /api/dashboard/agents-status` 中 `status.last_activity_at`;不新增 HTTP
请求。后端该字段是各 Agent 的 `MAX(events.created_at)`,因此覆盖 chat/job/Lark/
callback 等所有落 event 的活动,比 `AgentInfo.last_assistant_at` 更符合 Dashboard
的“最近活跃”语义。展示使用 locale-aware `formatMessageAge`,原始 ISO 时间保留在
title tooltip;从未产生 event 或状态请求失败时显示 `—`。

## 2026-08-24 — Agent 目录视觉收敛 + 真实 Framework / Model 列

Agents 落地页改为目录式信息架构:标题左侧 Bot 图标、紧邻标题的总数和右侧
New agent CTA;移除说明副标题、页内 Agents/Squads 切换、KPI、团队下拉、批量
加入/移出/删除工具条、复选框及 shown/selected/total 摘要。Squads 仍由侧栏写入
`?view=teams` 到达同一页面,只是页面内部不再重复放一组切换按钮。

Agent 表将原 Source 列替换为独立的 Framework / Model 两列。两列读取
`AgentInfo.agent_framework/model`,由后端 `GET /api/auth/agents` 批量解析有效配置
(agent override 优先,否则 owner default),避免每行调用一次 llm-config 的 N+1。
Name / ID 列改成 240–320px 的受限列,其余空间给 Teams 与长模型名;整列选择能力
随批量工具条一起删除,行展开、聊天入口和 kebab 行内管理保持不变。

## 2026-08-24 — 成为 Agents/Squads 的落地页;吸收侧栏聊天列表的行内操作

[[../../frontend/src/components/layout/Sidebar.tsx]] 撤掉了常驻的 chat
roster(`AgentList`),`view` 状态改由 `useSearchParams` 驱动
(`?view=agents|teams`,不带 view 或 `view=agents` 都算 agents)——侧栏的
Agents / Squads 两个导航行都路由到本页,靠这个 query param 决定打开哪张表;
本页头部原有的 segmented toggle 现在通过 `setSearchParams` 回写同一个
param,两处保持同步而不是各管各的 state。

Agents 表新增一列 action(chevron 展开详情的行为不变,单独加一个「进入聊天」
图标按钮 + [[../../frontend/src/components/layout/AgentRowMenu.tsx]] 复用
自退役的 `AgentList.tsx`);改名走行内 input(点 kebab 的 Rename 触发,
Enter/Esc/失焦提交),编辑简介开
[[../../frontend/src/components/layout/EditAgentDialog.tsx]],清空数据开
[[../../frontend/src/components/layout/ClearAgentDataDialog.tsx]],删除/
公开开关直接調 api。这批 state(`editingAgentId` / `clearTarget` /
`editTarget` / `deletingAgentId` 等)和对应 handler 都是从 `AgentList.tsx`
原样搬过来的,只是挂载点从侧栏行换成表格行。

Teams 表新增 **Leader**(解析 `team.lead_agent_id` 对应哪个 roster agent)
和 **Created by**(团队没有 owner 字段,统一显示当前用户
`displayName || userId`)两列,以及一个 action 列:「进入聊天」图标
(`/app/teams/:id/chat`)+ Manage 按钮(不变,仍开
[[../../frontend/src/components/teams/TeamManagementModal.tsx]])+
[[../../frontend/src/components/layout/TeamRowMenu.tsx]](加成员 = 复用
`useCreateAgent({ teamId })`、改名 = 行内 input、清空数据开
[[../../frontend/src/components/teams/ClearTeamDataDialog.tsx]]、删除直调
`useTeamsStore().deleteTeam`)。这一整批同样是从退役的 `TeamChatRow.tsx` /
`AgentList.tsx` 原样搬过来的。

`AgentList.tsx` / `AgentGroupSection.tsx` / `TeamChatRow.tsx` /
`agentGroupUtils.ts` 连同测试一并删除 —— 侧栏不再需要"渲染整个聊天列表"
这件事,行为搬到这两张表里之后,那批组件就是纯粹的重复代码。

## 2026-08-06 — Chat UI v4:吸收 ManageAgentsPage,监控 + 管理合一

页面重写为 v4 形态:标题 + Agents/Teams segmented 切换 + 4 个 KPITile
(Running / Queued / Errors / Cost today,仅统计 owned agents — public 无
metrics,规避 NaN)+ 搜索/团队过滤条 + 批量操作条(全选 / 选团队 /
加入 / 移出 / 删除,shift-click 范围选择保留)+ **状态×管理合一表**:
checkbox | chevron+头像+name/id | 状态点 | 团队 chips | 来源。行可展开
(**Set 多展开**,替代旧单 expandedId),展开内容复用 AttentionBanners /
QueueBar / MetricsRow / SessionSection / JobsSection / Sparkline /
RecentFeed。数据 = configStore.agents(roster)⨝ dashboardStore.agents
(status)按 agent_id;status 缺席显示 "—" 行。public agents(状态流里
非 roster)渲染只读行,保住旧 PublicCard 的可见性。

不变量全部沿袭:轮询 FSM 节奏归 dashboardStore.computeInterval;清理
必须 active=false + clearTimeout;429 → onRateLimited;tray badge 仅在
计数变化时 setTrayBadge;listenTauri 空返回用 ?.() 解绑。

Teams 视图:团队表(色点/名称/成员数/来源)+ Manage 按钮开既有
TeamManagementModal(内部不动)。AgentCard / DashboardSummary /
DurationDisplay / ConcurrencyBadge / dashboard 版 StatusBadge 删除;
ManageAgentsPage 删除(路由 / TopBar crumb / Settings nav 项一并清理)。
批量删除仍是逐行循环、部分成功弹窗 — 无事务语义,故意的。

> 2026-06-24: Renamed from the mis-named `DashboardPage.md` to the canonical
> `DashboardPage.tsx.md` and rewritten in English to house format. Behavior is
> unchanged — still the polling FSM dashboard mounted at `/app/dashboard`.

# DashboardPage.tsx — Agent Dashboard v2: a self-throttling polling status board

## Why it exists

The cross-agent operations view (`/app/dashboard`): a card grid showing every
agent's run health at a glance, separate from any single agent's chat. It is the
one screen that has to keep itself fresh without a websocket, so its real job is
to poll the backend status endpoint at a rate that adapts to whether the user is
actually looking — cheap when hidden/idle, responsive when focused.

## How it works / design

- **Polling FSM lives in [[dashboardStore]], not here.** The page is a thin view:
  it feeds the store the FSM inputs (`visibility` from the `visibilitychange`
  event, `tauriFocused` from Tauri `tauri://blur` / `tauri://focus`) and runs a
  self-rescheduling `tick()` whose next delay is `store.computeInterval()`. The
  store decides cadence from `visibility × tauri-focus × any_running`; an interval
  of `Infinity` parks the loop entirely (e.g. tab hidden, nothing running).
- **Tray badge is a side effect of polling.** After each successful fetch the
  page computes the running count and calls `setTrayBadge(running)` only when it
  changed (Tauri desktop; web mode is a no-op). This keeps the dock/tray count
  live without a separate loop.
- **429 is handled as backpressure, not an error.** A 429 routes to
  `store.onRateLimited()` (exponential backoff via `computeInterval`) instead of
  the red error banner; other failures go to `onFetchError`.
- **Upstream/downstream**: subscribes to [[dashboardStore]]; renders
  [[DashboardSummary]] (health legend/counts) over a grid of [[AgentCard]] (each
  card owns its own expand/collapse). Data via `api.getDashboardStatus`; tray via
  `lib/tauri`.
- **Gotchas**: the cleanup must set `active=false` AND `clearTimeout` or a stale
  `tick` keeps firing after unmount. `listenTauri` returns null off-desktop, so
  unlisten with `unlistenFn?.()`. One page-level `expandedId` means a single card
  expands at a time (a `Set` would be needed for multi-expand). `DashboardSummary`
  counts are aggregated frontend-side — public agents are forced to
  `healthy_idle` since they carry no `health`; if the backend ever adds health to
  public agents, fix the aggregation too.
