---
code_file: frontend/src/components/chat/team/TeamWorkspacePanel.tsx
last_verified: 2026-08-07
stub: false
---

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
