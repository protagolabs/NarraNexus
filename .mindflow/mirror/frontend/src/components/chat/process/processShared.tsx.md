---
code_file: frontend/src/components/chat/process/processShared.tsx
last_verified: 2026-08-30
stub: false
---

## 2026-08-30 — `∴` 行分两档:独白去斜体、提到 ink70

`ProcessEventRows` 的 thinking 行现在按 [[monologueTier]] 分档：独白
（`monologue=true` 且 [[uiStore]] 偏好开）**去掉 italic、色阶 ink50 → ink70**；
provider CoT 保持 italic ink50 的草稿纸观感。

**同一个 `∴` 字形、同一行结构、同一条轨** —— 分档是行内色调/字形的事，不是
新行类型。没有为它新造 glyph（会和 `»` 阶段行、`$` 工具行抢辨识度），也没有
动 `PHASE_STEP_IDS` 白名单：**独白不是相位**，不进那张表。

与 [[TurnTimeline]] 的关系是**同一条内容的两个生命周期阶段**，不是两处显示：
本面板只在 streaming 期间挂载，turn 结束后 ProcessPanel 卸载、过程随
[[segmentTurn]] 折进对应 reply 气泡由 TurnTimeline 渲染。两边都改，是为了
让这两个阶段看起来一致。

**偏好走 prop，不在本组件订阅 store**（review 第 2 轮定的）：
`ProcessEventRows({ process, showNarration })`，由面板级调用方
（[[ProcessPanel]] / [[TeamMemberPanel]]）经 [[useNarrationTier]] 读出后传进来。
第一版是组件自己 `useUIStore`，但这是个**被两个面板复用的共享渲染件**——
往里埋一个不在 props 上的输入，下一个复用它的面板就会遇到「events 传对了、
显示还是不对」。

**`showNarration` 刻意不给默认值**（review 第 3 轮）：第一版写了
`= true`，那等于把这条契约从**编译期强制**降成约定，而且默认方向还是反的
——忘了传 = 无视用户「关闭」，不是回落到安全侧。现在漏传由 tsc 当场抓住。

## 2026-08-26 — 阶段名对齐真实语义 + 新增 PHASE_STEP_IDS 白名单

`PHASE_LABEL_KEYS` 过去整体错位一格：step 1（叙事选择）显示「加载上下
文」、step 2（模块加载）显示「加载资源」、step 3（**Execute Agent
Loop**，其实已进 loop）显示「构建上下文」。现在按后端每步真实动作命名：
1=选择叙事 / 2=加载模块 / 2.5=同步实例 / 3=构建上下文 / **3.4=运行
Agent**（新增，对应后端把 step 3 拆成「构建上下文(3)」和「运行 Agent
(3.4)」两个相位——见 [[step_3_agent_loop]] 的 `PHASE_*_STEP`）。

新增导出 `PHASE_STEP_IDS`（**从 `PHASE_LABEL_KEYS` 的 keys 派生的 Set**，
单一事实源——加相位只改一处）作为**白名单**:只有这些顶层相位画成阶段
行。工具子步 `3.4.x`、`3.5` 最终思考回声、收尾 `4/5` 都不是「现在在干
嘛」，白名单同时杜绝未映射 step 的原始英文 title 泄漏。仅作成员判定,显示
顺序来自 `steps` 到达序、不是这个集合。

另抽出共享 `phaseSettled(phase, allSteps, hasProcessEvents)`:相位是否落定
（✓ vs spinner）的判据从两个面板各写一份收敛到这里（此前 [[ProcessPanel]]
读全量 steps、[[TeamMemberPanel]] 读过滤后的 phases，白名单生效后两者会
给不同答案）。**两侧都传未过滤的 steps**,让「后面的相位已出现」能看到
白名单之外的 run-agent/收尾 id。`parseFloat('3.4.1')===3.4`(第二个点截断)
故工具子步永不给 3.4 提前打勾——刻意如此,别改成按段比较。改一处两边跟
（铁律 #8）。

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
