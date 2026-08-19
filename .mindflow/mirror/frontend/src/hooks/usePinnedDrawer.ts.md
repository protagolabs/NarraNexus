---
code_file: frontend/src/hooks/usePinnedDrawer.ts
last_verified: 2026-08-19
stub: false
---

# usePinnedDrawer — 钉选抽屉行为的唯一 React 接线

pin 状态(readInitialDrawerPinned+写回)、用户宽度(只在拖拽释放持久化——
WIDTH key 必须表示「用户选过宽度」,首跑判据依赖这一点)、rAF 合并的
viewportW、渲染期 clampDrawerWidth、colRef+两相拖拽。策略/键在
[[../components/layout/drawerLayout]];消费方:[[../components/layout/MainLayout]]
(单聊)与 [[../components/chat/team/TeamChatPanel]](团队房间)——
**一份实现、一份偏好**,右栏行为在两处必然一致。
