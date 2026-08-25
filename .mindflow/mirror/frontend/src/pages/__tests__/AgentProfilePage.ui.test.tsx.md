---
code_file: frontend/src/pages/__tests__/AgentProfilePage.ui.test.tsx
last_verified: 2026-08-25
stub: false
---

# AgentProfilePage.ui.test.tsx — Profile 信息架构回归

用窄 store/API/重面板替身验证 Profile 的产品边界:Overview 必须挂载
[[AgentOverviewCard.tsx]](此处浅替身,断言它拿到正确的
frameworkLabel/modelLabel/taskLabel/jobsCount/inboxCount props,不在
这个文件里重复验证卡片内部的图标对/flag/chip 逻辑——那部分覆盖在
`AgentOverviewCard.test.tsx` 里)并挂载真实的 Jobs/Inbox 面板边界;
Capabilities 只列出 Network、Memory、Skills、MCP、Channels,默认只打开
一个原子能力面板。Settings 必须位于顶层 Tab,并按 General、Awareness、
Model & Framework 组织;General 水合名称与描述,Awareness 仍挂载既有
原子面板。Header 还必须复用 Team 双色头像与悬浮介绍,并显示 Team 语义
图标——Framework/Model 2026-08-25 起从 Header 搬进了
[[AgentOverviewCard.tsx]],不再在 Header 断言里覆盖。配置卡片
(Settings → Model & Framework)不受影响,仍要求同时带字段语义图标与
品牌图标、展示有效配置并提供 Configure 动作。Tooltip primitive 在此
替换为同步测试壳,测试聚焦产品 payload 与样式契约,不重复覆盖 Radix 的
延迟挂载行为。
