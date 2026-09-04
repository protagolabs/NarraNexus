---
code_file: src/xyz_agent_context/module/nexus_plugins_module/_nexus_plugins_impl/test_runner.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2f.1）— `plugin_test`：有界子进程跑插件自己的 pytest，产出签名报告

超时 300s、RLIMIT_AS 2GiB、RLIMIT_FSIZE 256MiB、固定环境（PYTHONPATH=仓库 src、临时插件 home、local 模式），
没有 tests/test_*.py 直接红（没测试不能 register）。报告含 `tree_hash` 与 `report_hash`（sha256 签名），
register 只认「当前树哈希 + 绿报告」。
