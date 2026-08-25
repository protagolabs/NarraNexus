---
code_file: frontend/src/pages/AgentProfilePage.tsx
last_verified: 2026-08-25
stub: false
---

## 2026-08-25 (4) — Overview 顶部三卡 + 头部 Framework/Model 合并成一张归属信息卡

Owner 反馈 Current Task / Jobs / Inbox 三张独立 `PaperCard` 摆在
Overview 顶部,视觉上像可点入口,实际都不可点,是纯粹的 agent 归属
信息;头部 meta 行里的 Framework/Model 又跟 team/last-active 混排,
语义上更该跟"这个 agent 是什么"放在一起。改法:三卡 + 头部
Framework/Model 合并进新组件 [[AgentOverviewCard.tsx]],再补上
Skills/MCP 摘要(此前 Overview tab 完全看不到,只能去 Capabilities
tab)。设计定稿过程(三轮视觉稿迭代,最终选定"纵向清单 + 无边框圆角
tile,不用分割线"的方向)记录在
`reference/self_notebook/specs/2026-08-25-agent-profile-overview-card-
design.md`。头部 meta 行删掉 framework/model 两个 `MetaItem`,只留
agentId/team/last-active 三项;`SummaryCard` 局部组件随三卡一起
删除,不再被引用。

## 2026-08-25 (3) — Chat 放大,Delete 收进 "⋮" 抽屉

Owner 复审 (2) 的结果:Chat 按钮该更醒目(放大,不再是 Dashboard 表格行那种
紧凑尺寸)、Delete 不该跟 Chat 平级摆在一起(误触代价太高),要收进一个
"⋮" 点开的小抽屉里——参照给的参考图(浅色 Chat pill + 黑色主操作 + "⋮" →
点开弹出白底红字的 Delete 项)。改法:`agentChatButtonClass` 的字号/内边距
从表格行尺寸(`text-[11px]` / `px-2.5 py-1.5`)放大到 `text-sm` /
`px-4 py-2`,圆角跟着从 `radius-xs` 升到 `radius-sm`——保持"浅色卡片描边、
非 ink 填充"的样式家族,但不再要求跟 Dashboard 逐字节一致(尺寸语境不同)。
新增 `AgentHeaderMenu` 局部组件承接 Delete:样式与交互抄自已退役的
[[AgentRowMenu.tsx]](click-outside 遮罩 + 右对齐绝对定位面板),但**没有
复用那个组件本身**——它带 rename/edit/clear/public-toggle 四个跟 Delete
无关的入口,而这四个操作本页 Settings tab 已经各有归宿,硬套进来等于为了
第五个入口多接四个不需要的 handler。菜单项文案复用已有的
`layout.agentRowMenu.options`/`layout.agentRowMenu.delete`(触发按钮
aria-label / 菜单项 label),确认弹窗文案仍是 (2) 里选定的
`layout.agentList.deleteAgentTitle/deleteAgentMessage/deleteAction/
deleteFailedTitle/deleteAgentFailedMessage` 五个 key,删除流程本身
(`useConfirm` → `api.deleteAgent` → 成功 `navigate` / 失败 `alert`)不变。

## 2026-08-25 (2) — 头部 Chat 按钮去黑,加 Delete(后续 (3) 里改成抽屉里)

Owner 反馈头部的 Chat 按钮用 `Button variant="primary"` 是纯黑填充,视觉太重,
要求跟 Dashboard 智能体列表行的 Chat 按钮同款样式。改法:不复用 `Button`
组件,直接照抄 [[DashboardPage.tsx]] `agentChatButtonClass` 的类名字符串
(浅色卡片描边 pill,而不是 ink 填充)。同时在旁边加一个 `Button
variant="danger"` 的 Delete 按钮,复用已有的
`layout.agentList.deleteAgentTitle/deleteAgentMessage/deleteAction/
deleteFailedTitle/deleteAgentFailedMessage` 五个 i18n key,删除走
`useConfirm()` 确认弹窗(`danger: true`)→ `api.deleteAgent` → 成功后
`navigate('/app/dashboard?view=agents')`,失败弹 `alert`。本页此前没有引入过
`@/components/ui`,新增的 `useConfirm` 是本页第一次跨这两套组件系统
(`@/components/nm` 的 `Button` + `@/components/ui` 的
`useConfirm`)——两者互不依赖,可以在同一文件混用。Delete 按钮本身的位置
(跟 Chat 平级)被 (3) 推翻,收进了 "⋮" 抽屉。

## 2026-08-25 — breadcrumb 返回目标按来源区分:Dashboard vs Chat

Owner 指出:profile 页现在有两个入口——Dashboard 列表行(`DashboardPage.tsx`)
和 Chat 头部头像(`ChatHeader.tsx` 的 `goToProfile`),原来 breadcrumb 一律
`navigate('/app/dashboard')`,从 Chat 进来的用户点返回却被送去 Dashboard,
不是原路返回。改动:两个调用方 `navigate(...)` 时都带上
`state: { from: 'chat' | 'dashboard' }`;本页新增 `ProfileEntryState` 类型,
用 `useLocation().state` 读出来源,`cameFromChat` 为真时 breadcrumb 文案换成
`pages.agentProfile.backToChat`("Chat"/"对话"),点击复用既有 `openChat()`
(而不是裸 `navigate('/app/chat')`)以确保 configStore/chatStore 的活跃 Agent
先被正确设置;否则回退到原来的 `navigate('/app/dashboard')`。`location.state`
在刷新页面后会丢失,此时按设计回退到 Dashboard——这是可接受的默认值,不是 bug。

# AgentProfilePage.tsx — Agent 的独立工作与能力档案

## 为什么存在

Dashboard 是可扫描的目录,不应因展开某一行而被大块运行详情打断。本页承接
`/app/agents/:agentId`,把单个 Agent 的工作状态、真实操作面板和能力配置组织成
稳定的 Profile。进入页面时把路由 Agent 写入 configStore,从而让 MainLayout
既有 preload/auto-refresh 链路为正确 Agent 水合数据。

## 信息架构

- Overview:归属信息全部收进 [[AgentOverviewCard.tsx]] 一张只读卡——
  Framework/Model(原页头 MetaItem)、Current Task(读
  `AgentInfo.active_run` 与 dashboard status)、Jobs/Inbox 计数(读
  preloadStore)、Skills/MCP 摘要(卡内部自己订阅
  `useSkillsList`/[[useMCP.ts]],不经过页面 props)。整卡不可点击,与
  下面两个真正可点击的完整面板区分开;两个完整面板仍直接复用
  `JobsPanel embedded` 与 `AgentInboxPanel embedded`,不复制数据访问
  或操作逻辑。
- Capabilities:左侧原子目录一次只挂载一个能力。Network/Memory/Skills/MCP/
  Channels 复用 `BookmarkPanelHost` 的既有 tab 映射。
- Settings:位于 Capabilities 右侧,左侧顺序固定为 General → Awareness →
  Model & Framework。General 复用现有 `api.updateAgent` 保存名称与描述,保留 255
  字符前端约束并显示当前身份头像预览;Awareness 复用 `BookmarkPanelHost`;
  Model & Framework 显示 agents 列表中的有效值,配置动作打开既有
  `AgentLlmConfigPanel`。配置入口集中在 Settings,不再重复出现在 Capabilities。
- Header:Agent 身份、运行状态、Team、Last active,以及放大版浅色 Chat
  pill + "⋮" `AgentHeaderMenu`(Delete 收在里面,红字)。Framework/Model
  2026-08-25 起搬进 Overview 的 [[AgentOverviewCard.tsx]],不再出现在
  页头(详见上方 changelog (4))。Team 使用双人语义图标加共享
  `AgentTeamAvatars`,因此与 Dashboard 保持同一套双色重叠头像和悬浮
  Profile。返回 breadcrumb 按入口来源(`location.state.from`)分别指向
  Dashboard 或 Chat(2026-08-25,详见上方 changelog),因此 MainLayout
  不再为此路由叠加浮动 X。

## 数据与性能边界

页面直接打开时补拉一次 dashboard status;Agent 列表与 Teams 沿用 store refresh。
Capabilities 与 Settings 都保持“一次一个重面板”的原子挂载约束,避免 Awareness、
Network、Memory、Skills、MCP 和 Channels 同时发请求或加载重依赖。General 沿用
现有 Agent 更新接口,Model 配置沿用既有面板；页面不新增后端接口。
