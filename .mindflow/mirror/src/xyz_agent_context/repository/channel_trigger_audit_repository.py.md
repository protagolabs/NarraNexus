---
code_file: src/xyz_agent_context/repository/channel_trigger_audit_repository.py
stub: false
last_verified: 2026-08-10
---

# channel_trigger_audit_repository.py — 通用渠道审计仓库

## 2026-08-10 — append 镜像一条 AUDIT 级日志行

sink 运的是日志记录不是 DB 行——不镜像的话 denied/silent/attachments/
processed 只能靠 pull 才可见。自定义 AUDIT 级(25)仅在 setup_logging
后存在,裸库使用(测试/脚本)回落 INFO 而不是丢行。
