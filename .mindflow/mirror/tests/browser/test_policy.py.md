---
code_file: tests/browser/test_policy.py
last_verified: 2026-09-23
stub: false
---

# 独立浏览器能力策略

普通网址访问不再进入策略判定。保留上传、下载和高级脚本的独立默认值、origin/通配符匹配、
配置优先级、能力隔离、作用域期限与序列化回归；历史访问数据的退役由 unrestricted 用例覆盖。
