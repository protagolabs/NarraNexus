---
code_file: frontend/src/lib/tokenFormat.ts
last_verified: 2026-08-19
stub: false
---

# tokenFormat.ts — token 数与美元的统一渲染规则

## 为什么存在

2026-08-19 账户页新增「NarraNexus 用量」区（[[NarraUsageSection.tsx]]）时，需要的
两个函数 [[CostPopover.tsx]] 里已经有了。**两份独立的「token 怎么渲染」会漂，而且是
静默地漂** —— 同一周的用量在一个屏幕读 1.2M、另一个读 1.23M，读者无从判断哪个被
四舍五入过。于是从 CostPopover 抽出，两边共用。

规则本身没变（`<1000` 原样 / `<1M` 一位小数 k / 否则两位小数 M；`formatCost` 的
`<$0.0001` 兜底见 [[CostPopover.tsx]] 2026-08-03 条目——**那条推理的正本在那边**，
不要在这里重述一遍再让两处慢慢说不同的话）。

## Gotcha：还有第三份

`components/chat/InnerThoughtCard.tsx` 仍带一份自己的 `formatTokens`，M 档是**一位**
小数（本文件是两位）。合并它会改变那张卡的渲染和它的测试断言，属于另一件事，没有
夹带进这次的计费文案修复；记在 `reference/self_notebook/todo/`。
