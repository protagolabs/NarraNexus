---
code_file: frontend/src/components/chat/team/TeamWorkspacePanel.tsx
last_verified: 2026-08-07
stub: false
---

## 2026-08-07 (三次) — 改为纯展示组件，选中态由父级控制

数据获取上提到 [[TeamChatPanel.tsx]]。原因：消息下的芯片和面板列表**必须对「现在打开的是
什么」有一致认知**，所以状态只能有一个所有者，而它必须是两者共同的父级。两个组件各自拉同一份
数据也是重复请求。

预览窗格在列表**下方的独立区域**，因此芯片选中时**不切 tab**——切了会把用户从 Files 浏览中
拽走，而预览本来就与当前 tab 无关。（初版用 effect 强制切 tab，被 lint 的
`set-state-in-effect` 挡下，顺着提示发现那个同步本身是多余的。）

## 2026-08-07 (二次) — 改为面板内联渲染，并发现团队 view-token 是多余的

初版点击 artifact 走 `window.open` 新标签页。改为在面板下半部内联渲染
（复用 [[ArtifactRenderer.tsx]]）——这个面板存在的意义就是**边读对话边看产出**，新开标签页
恰好把那个上下文丢掉。

**关键发现**：`ArtifactRenderer` 零 store 耦合，且内部按 `artifact.agent_id`（**产出者**）走
agent 侧 view-token。而 agent 路由的鉴权是「JWT 用户是否拥有该 agent」——团队成员本来就都是
团队 owner 的 agent，所以**队友的 artifact 用既有路由就能取到**，无需任何团队专用 token 通道。

因此 `POST /teams/{id}/artifacts/{id}/view-token`（commit 6209dba5）在当前 UI 路径上**用不到**。
保留与否见该 commit 的说明与 owner 决定；它表达的是「按 team 归属授权」这一更贴切的语义，
但功能上与 agent 路由重叠。

# TeamWorkspacePanel — 团队房间的工作台

## 为什么存在

团队的产出此前无处安放：team turn 注册的 artifact 只出现在**产出者的私聊**里，想看「团队做了
什么」得离开房间、点进某个成员的一对一会话；共享文件则完全没有 UI，找一个文件只能靠 agent 在
聊天里念绝对路径。

## 关键设计

**一个面板两个 tab，而不是两个入口**（§5.2 定案）。心智模型是「团队的工作台」；拆成两处等于
要求用户**先知道自己要找的是哪一类东西**才能开始找。

**每一行都标注归属。**私聊里「这是谁做的」从来不是问题；团队房间里多个 agent 写进同一个空间，
不标注的列表就是共享工作台退化成匿名堆放的方式。

**两个 tab 一起加载**：计数显示在 tab 标签上，懒加载会让未打开的那个 tab 谎称有 0 项。

**打开 artifact 走团队 view-token**（[[teams.py]]），不是 agent 侧那条——后者要求调用方 agent
**就是**产出者，在团队里恰好是反的。

**挂在 transcript 旁边而非独立路由**：「我们做了什么」是**边读对话边问**的问题。
`refreshKey` 取消息数，使得产出 artifact 的那一轮无需用户手动刷新即可显现。
