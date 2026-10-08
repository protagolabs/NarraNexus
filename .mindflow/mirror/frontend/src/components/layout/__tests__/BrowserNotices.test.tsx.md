---
code_file: frontend/src/components/layout/__tests__/BrowserNotices.test.tsx
last_verified: 2026-10-08
stub: false
---

# 登录通知送达

钉住：轮询当前 agent、无请求不渲染、无 agent 不轮询；打开浏览器但不接管；接管期间仍可见、服务端
移除后才消失；别的 agent 的条目不显示；上一个 agent 迟到的响应/错误不能覆盖当前；切换 agent 立即
隐藏；慢轮询不重叠、卸载后停止；轮询失败清掉旧通知并可手动重试；禁用浏览器插件即停止轮询。
