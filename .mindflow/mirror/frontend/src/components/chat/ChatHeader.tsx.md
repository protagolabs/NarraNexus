---
code_file: frontend/src/components/chat/ChatHeader.tsx
last_verified: 2026-08-25
stub: false
---

## 2026-08-25 (5) — 移除 "session · 时间" 标签

Owner 反馈这个 mono 小字("session · 09:41"/"会话 · 09:41")没有实际
用处,是最后一条可见消息的时间戳,不是会话起始时间也不会实时跳动
(见 [[ChatPanel.tsx]] 原 `sessionLabel` useMemo)。改动:`ChatHeaderProps`
删掉 `sessionLabel` 字段,组件不再渲染这段 `<span>`;调用侧
`ChatPanel.tsx` 同步删掉 `sessionLabel` 的计算(连带唯一消费者消失的
`formatChatTimestamp` import,该函数本身在 `MessengerSection.tsx` 还有
别的调用点,没有删定义)和传参。i18n key `chat.header.sessionLabel`
在全部 10 个 locale 文件里同步删除(唯一调用点消失)。

## 2026-08-25 (4) — ⋯ 菜单再移除 Model & framework,与 Profile 页完全去重

Owner 追问"model/framework还在啊"——2026-08-25 (2) 只删了 Awareness/
Network/Memory,漏了同样在 Agent Profile 页(Settings → "Model &
Framework",点 Configure 打开同一个 `AgentLlmConfigPanel`)已有对等入口的
Model & framework 按钮。改动:菜单里的按钮 + 它前面那条分隔线一并删除;
`onOpenAgentConfig` prop 从 `ChatHeaderProps` 和组件签名里删掉;
`SlidersHorizontal` 图标导入删除(唯一调用点消失)。⋯ 菜单现在只剩
config 五项(workspace/channels/skills/mcp/smarthome),不再有分隔线。
调用侧改动见 [[ChatPanel.tsx]]:`agentCfgOpen`/`onOpenAgentConfig` 传参、
`<AgentLlmConfigPanel>` 挂载、`modelReloadKey` 一并清掉——它们只为这个
入口存在,入口没了就是死代码。i18n key `chat.header.modelFramework`
在全部 10 个 locale 文件里同步删除(唯一调用点消失)。快捷模型切换
（`ComposerModelBadge`,composer 工具行右侧）不受影响,继续独立工作。

## 2026-08-25 (3) — goToProfile 带 state: { from: 'chat' }

Profile 页现在有两个入口(这里 + `DashboardPage.tsx` 的 Agent 行),
[[../../pages/AgentProfilePage.tsx]] 的返回 breadcrumb 需要区分该回 Chat
还是回 Dashboard。`goToProfile` 的 `navigate()` 调用加了
`{ state: { from: 'chat' } }`;本文件内部行为不变。

## 2026-08-25 (2) — ⋯ 菜单移除 Awareness / Network / Memory,收敛去重

Owner 观察:Awareness、Network(social)、Memory 这三个面板已经完整地
出现在 Agent Profile 页(`AgentProfilePage.tsx` 的 Settings/Capabilities
tab,同样经由 `BookmarkPanelHost` 渲染),⋯ 菜单里再放一份是纯重复入口。
改动:`DETAIL_GROUP_A` 里删掉 `'awareness'`;`DETAIL_GROUP_B`(`social` +
`memory`)整个数组和它的渲染块一并删除,只留一条分隔线把剩下的 config
六项(现为五项:workspace/channels/skills/mcp/smarthome)和 Model &
framework 按钮隔开。`DetailItem` 里那条只为 `social` 服务的
`stripLabelKey` 特判也一并清掉(唯一调用点消失,留着就是死代码)。
面板本身(`AwarenessPanel` / `NarrativeList`)、`tabs.ts` 注册表、
`BookmarkPanelHost`、Profile 页均未改动——只是拆掉了聊天侧这一扇门,
房间本身还在,继续经 Profile 页可达。

## 2026-08-25 — 头像+agent 名改为跳转 profile 页,不再开 ⋯ 面板

Owner 要求:左上角头像原本和 agent 名共用 `detailOpen` 状态,点击会弹出
⋯ 菜单同款的面板抽屉——但用户预期点头像应该直接进 Agent 的 profile 页
(`/app/agents/:agentId`,已存在)。改动:头像 + agent 名合并进同一个
`<button>`,onClick 换成 `navigate(/app/agents/${agentId})`;不再调用
`setDetailOpen`,`ChevronDown` 图标随之移除(不再是下拉触发器,不需要
方向提示)。⋯ 图标(右侧)保留原状,继续是打开该面板抽屉的唯一入口——
`detailOpen` state 和 `DETAIL_GROUP_A/B` 渲染逻辑本身未变,只是失去了
一个旧触发点。i18n key `chat.header.agentDetailTitle` 改名为
`chat.header.viewProfile`(所有 10 个 locale 文件同步),语义从"展开
详情菜单"变成"查看 agent 资料"。

## 2026-08-11 — agent 名与侧栏同字族(sans),主角感靠字号/字重

Owner 对照截图指出头部 "New Agent" 与侧栏行观感不一致:此处原为
Space Grotesk 15px semibold,侧栏行是 Inter 14px medium——同名同屏双字族。
按 design_system.md §4.1(display 只用于大标题),改为 `--font-sans`
text-base(16px)semibold:字族与侧栏统一,「主角」层级由字号+字重承担。

## 2026-08-06 (2) — Artifacts 入口改开 drawer + 换 glyph

图标从 FileText 换成 ArtifactsGlyph(tabs.ts,Owner 截图指定);点击从
"切 artifactStore.collapsed" 改为 openPanel('artifacts') — 与其他条目
完全同构。徽标仍读 artifactStore.artifacts.length。

# ChatHeader — v4 聊天头部(agent 名主角)

## 为什么存在

Chat UI v4 把三层头部(品牌行 / tab 行 / 安全横幅)压成一行,并接管了
被退役的右缘 BookmarkStrip 的全部入口职责。桌面专属(hidden md:flex);
移动端由 MainLayout 顶条 + ChatPanel 里 md:hidden 的 tab 行代替。

## 结构

左:侧栏展开钮(仅 sidebarCollapsed 时)→ RingAvatar(silicon)+ agent 名
合并成一个按钮(点击跳转 `/app/agents/:agentId`,与 ⋯ 菜单互不干扰)。
("会话 · 时间" mono 标签已于 2026-08-25 (5) 移除。)
右:ExecutionPopover(流式时)→ Chat/Inner Thoughts segmented(状态在
ChatPanel)→ Jobs / Inbox / Artifacts 图标(徽标来自
deriveTabStatus / artifactStore)→ CostPopover → ⋯ detail 菜单。

## 设计决策

- **只做门,不做房间**:⋯ 菜单里的条目都通过 uiStore.requestPanel 打开
  ChatView 里既有的 BookmarkDrawer 面板;面板内部零改动(Owner
  2026-08-06 口头确认:设计稿未提到的界面细节保持不变)。头像+agent 名
  是唯一例外——它是纯路由跳转(profile 页),不经过 requestPanel。
- ⋯ 菜单顺序(2026-08-25 (4) 起):只剩 config 五项(workspace/channels/
  skills/mcp/smarthome),无分隔线。Awareness / Network / Memory / Model &
  framework 均已从此菜单移除,理由见上方 2026-08-25 (2)(4) 条目——它们
  在 Agent Profile 页都有对等入口,不需要在聊天侧重复一份。
- Artifacts 图标切 artifactStore.collapsed(列自身有 sliver 逻辑)。
- 徽标/markTabOpened 语义沿用 tabs.ts 注册表,不另造信号源。
