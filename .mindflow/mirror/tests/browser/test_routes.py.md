---
code_file: tests/browser/test_routes.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

审批端点改为 notices；钉住旧审批端点 404、安装/取消安装在云端 403 且不触发安装。

# 浏览器路由边界

运行时接口及流连接继续验证身份与所有权、错误处理和跨进程连接。会话夹具使用空策略，
普通网页无需额外允许字段；高级权限模型的 HTTP 请求形状由 policy_management 覆盖。
流接口夹具实现单次补帧接口；真实缩放与画面一致性由浏览器集成测试负责。
