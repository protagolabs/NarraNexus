---
code_file: tests/browser/test_routes.py
last_verified: 2026-09-23
stub: false
---

# 浏览器路由边界

运行时接口及流连接继续验证身份与所有权、错误处理和跨进程连接。会话夹具使用空策略，
普通网页无需额外允许字段；高级权限模型的 HTTP 请求形状由 policy_management 覆盖。
流接口夹具实现单次补帧接口；真实缩放与画面一致性由浏览器集成测试负责。
