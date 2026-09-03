---
code_file: frontend/src/components/chat/team/__tests__/TeamManagePanel.test.tsx
last_verified: 2026-09-03
stub: false
---

# TeamManagePanel.test — 团队管理 tab 的 9 条守卫

2026-09-03 新建。公告栏在最上;组长 select 回调;patrol 从板子接口读、经 PUT 写、失败回滚;
成员增删走 store;编辑资料开弹窗;清理数据把 scopes 回报给房间;删除先 confirm 再离开房间,
拒绝则不删。弹窗/清理对话框/公告栏面板都 mock 成可挂载的壳。
