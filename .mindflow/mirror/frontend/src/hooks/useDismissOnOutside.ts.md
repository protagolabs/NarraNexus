---
code_file: frontend/src/hooks/useDismissOnOutside.ts
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — extraRefs、Escape 语义、iframe blur、DEV 警告

- 新第三参 `extraRefs`:portal 形态(trigger 与面板不同父)把两个 ref 都
  交进来,任一 `contains(target)` 即视为内部。[[../components/artifacts/ArtifactDownloadMenu]]
  借此收编,仓内不再有第二套手写「点外面关闭」。
- **Escape 被弹层吃掉**(`stopPropagation`):最顶层弹层优先,包裹它的
  Dialog 不会同一击被关——这是刻意语义,若将来要「一路关到底」需显式改这里。
- iframe 盲区:跨源 iframe 内的点击到不了 document,补 `window blur` +
  `activeElement 为 IFRAME` 才 dismiss(切标签/切应用的 blur 不带 IFRAME
  焦点,弹层正确保留)。
- DEV 下 active 而无任何 ref 挂上时 console.warn——忘挂 ref 的表现恰是
  本 hook 要修的那个 bug,必须吵。

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
