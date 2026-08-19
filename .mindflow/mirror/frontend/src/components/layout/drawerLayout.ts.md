---
code_file: frontend/src/components/layout/drawerLayout.ts
last_verified: 2026-08-19
stub: false
---

# drawerLayout — 书签抽屉的尺寸与持久化策略

从 [[MainLayout]] 抽出的纯函数模块:localStorage 键(含独立的
`DRAWER_FIRST_RUN_KEY`)、`DEFAULT_DRAWER_PX`、`maxDrawerPx(vw)` =
`clamp(MIN, min(0.6·vw, vw−672))`(672 = 侧栏 272 + 聊天最小 400)、
`clampDrawerWidth`、`readInitialDrawerPinned`(缺省钉选,只认显式 '0')、
**首跑对**:`shouldAutoOpenFirstRun`(只读,render 安全;小视口 false 且
不烧标记)+ `markFirstRunSeen`(effect 里写)。首跑标记是自己的 key——
手动开过面板(OPENED_ONCE)不花掉桌面教学卡。
改上限/默认值只动这里;MainLayout 只消费。测试 drawerLayout.test.ts 钉住
「新档案默认钉选」「大屏可 ≥ 半屏」「永不挤掉侧栏+聊天最小宽」。
