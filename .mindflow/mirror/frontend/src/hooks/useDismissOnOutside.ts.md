---
code_file: frontend/src/hooks/useDismissOnOutside.ts
last_verified: 2026-08-19
stub: false
---

# useDismissOnOutside — 弹层的「点外面/Escape 关闭」唯一实现

document 级 capture pointerdown + keydown 监听,替代全屏 backdrop `<div>`。
backdrop 方案的坑:`position: fixed` 相对最近的 **transform 祖先**布局,
弹层长在带动画的行里(`animate-slide-up` fill: forwards 保留 transform)时,
"全屏"遮罩只盖住那一行——这正是「点页面别处关不掉弹窗」的根因。

约定:返回的 ref 挂在**同时包含触发器和面板**的容器上;容器内交互不 dismiss。
回调经 latest-ref 转发,调用方可放心传内联闭包(不会引起监听重订阅)。
capture 阶段监听,中途的 stopPropagation 拦不住它。

消费方:[[../components/layout/AgentRowMenu]] / [[../components/layout/TeamRowMenu]] /
[[../components/layout/CreateMenu]] / [[../components/layout/Sidebar]](账户弹层)。
新弹层一律用它,别再手写 backdrop。
