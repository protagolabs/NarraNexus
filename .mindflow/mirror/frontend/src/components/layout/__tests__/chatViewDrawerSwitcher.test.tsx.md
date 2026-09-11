---
code_file: frontend/src/components/layout/__tests__/chatViewDrawerSwitcher.test.tsx
last_verified: 2026-09-11
stub: false
---

# chatViewDrawerSwitcher.test.tsx — single chat wires the drawer title switcher

## 为什么存在

组件级行为在 [[../../bookmarks/__tests__/drawerPanelSwitcher.test.tsx]];这里钉住单聊调用方
[[../MainLayout.tsx]] 的 `ChatView` 真的把 `activeTab / onSelectTab / switcherCategories` 传给了
抽屉(Owner 2026-09-11 要求)。渲染真实 `ChatView`(重型子组件 mock 掉,`BookmarkPanelHost`
换成按 tab 出 testid 的桩),`openPanel('artifacts')` 打开抽屉 → 点标题 → Artifacts 打勾、
无 studio 时不出 `builder` → 选 Jobs → 面板换成 jobs,且选之前清掉的 `DRAWER_OPENED_ONCE_KEY` 被重新写入
(证明切换器走的是 uiStore 面板漏斗而非本地 setState)。去掉三个 prop 中任意接线、或把 `onSelectTab`
改回 `setDrawerTab` 即红。
