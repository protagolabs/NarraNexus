---
code_file: tests/browser/test_policy_management.py
last_verified: 2026-09-23
stub: false
---

# 高级脚本管理回归

公开视图只包含 full_cdp_access，显式保存与撤销仍有所有权和输入校验。真实 SQLite 用例验证
并发脚本修改不会丢失独立文件能力的答复。HTTP 明确拒绝 access=allow/ask/deny 和旧撤销请求，
失败不修改原策略；测试不假设其他用例尚未写入任何规则。
