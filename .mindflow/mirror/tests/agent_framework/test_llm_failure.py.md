---
code_file: tests/agent_framework/test_llm_failure.py
last_verified: 2026-09-23
stub: false
---

# 模型错误分类与脱敏

覆盖共享严格凭据判定、宽松鉴权判定及展示文本脱敏。真实鉴权异常、401/403、缺失
密钥仍识别；模块名、token 数量、请求标识不能误判。服务不可用或超时即使提到 API key
也不证明密钥被拒绝；明确鉴权状态码或 SDK 鉴权异常的优先级保持最高。
