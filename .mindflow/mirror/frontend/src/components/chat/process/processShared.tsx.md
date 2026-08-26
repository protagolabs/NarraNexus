---
code_file: frontend/src/components/chat/process/processShared.tsx
last_verified: 2026-08-26
stub: false
---

## 2026-08-26 — 阶段名对齐真实语义 + 新增 PHASE_ORDER 白名单

`PHASE_LABEL_KEYS` 过去整体错位一格：step 1（叙事选择）显示「加载上下
文」、step 2（模块加载）显示「加载资源」、step 3（**Execute Agent
Loop**，其实已进 loop）显示「构建上下文」。现在按后端每步真实动作命名：
1=选择叙事 / 2=加载模块 / 2.5=同步实例 / 3=构建上下文 / **3.4=运行
Agent**（新增，对应后端把 step 3 拆成「构建上下文(3)」和「运行 Agent
(3.4)」两个相位——见 [[step_3_agent_loop]] 的 `PHASE_*_STEP`）。

新增导出 `PHASE_ORDER`（`0/1/2/2.5/3/3.4`）作为**白名单**：只有这些
顶层相位画成阶段行。工具子步 `3.4.x`、`3.5` 最终思考回声、收尾 `4/5`
都不是「现在在干嘛」，白名单同时杜绝未映射 step 的原始英文 title 泄漏。
[[ProcessPanel]] 与 [[TeamMemberPanel]] 都改用它（改一处两边跟，铁律 #8）。

## 2026-07-31 — 终端语言三件套沉淀（LiveDot / PhaseRow / LiveCursorRow）

ProcessPanel 的 ping 呼吸灯、`✓`/spinner 阶段行、`❯▌` 活光标抽成共享
组件：单聊面板与 team 的 [[TeamMemberPanel]] 同一套字形语言，改一处
两边跟（铁律 #8）。ProcessPanel 已切换为消费这三件。

# processShared.tsx — ProcessPanel 与团队成员详情共用的渲染件

## 为什么存在

单人聊天的 [[ProcessPanel]] 和团队房间成员栏的过程详情要长得一模一样——
同一套终端风格（∴ 思考 / $ 工具 / ↳ 输出、pending 琥珀 spinner、
friendly tool name 规则）。样式只能有一处事实源，否则两边会漂移。

## 内容物

- `ProcessEventRows`：TurnEvent[] → 终端行序列。纯渲染，无滚动无状态，
  过滤留给调用方；非 process 型事件返回 null。
- `deriveActivity` + `Activity`：折叠态"当前在干什么"一行的推导
  （最后一个 tool_call/thinking 赢，否则最后一个 pipeline 相位）。
- `PHASE_LABEL_KEYS` / `friendlyToolName` / `formatElapsed`。

## 上下游

消费者：ProcessPanel（单人 chat）、TeamRosterPanel 的成员详情（团队）。
输入契约是 TurnEvent 联合类型（types/index），历史回放侧由
`timelineToEvents` 归一到同一契约。
