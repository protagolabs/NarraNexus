---
code_file: frontend/src/components/chat/team/TeamSystemLine.tsx
last_verified: 2026-08-12
stub: false
---

# TeamSystemLine — 房间写的行，不是成员写的

三种：owner 停止了某个 run、公告栏变更、巡查盘点。三者都是平台在叙述自己，
所以都不给头像、不给身份色、不进气泡——**把平台事件打扮成成员消息，等于把它归给恰好触发它的那个人**，
而巡查那种情况下会归给一个解析不出任何成员的 `team_<id>` 标记。

markup 和理由都是从 [[TeamChatPanel]] **原样搬出**的，只换了位置：让 [[TeamTranscript]] 能决定
系统行**放在哪**，而不必同时拥有它**长什么样**。
