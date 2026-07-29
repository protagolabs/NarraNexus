---
code_file: src/xyz_agent_context/agent_framework/nexus_loop/contracts/errors.py
last_verified: 2026-07-29
stub: false
---
# contracts/errors — 七类错误封闭词汇(A5)

前六类镜像 loop/events.py 的 CLI_ERROR_TYPES;第七类 CONTEXT_OVERFLOW 是信号不是失败(触发被动压缩+重试)。legacy_error_type() 保证新词汇永不漏给旧消费链(overflow 未压缩兜底映射 invalid_request)。
