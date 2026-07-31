---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/session/error_classifier.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — prefill 拒绝串表插在类名表之前

网关某些后端拒绝「以 assistant 结尾」的对话(400 "does not support assistant
message prefill")。包装它的是普通 litellm BadRequestError,若走类名表会被判成
死路一条的 INVALID_REQUEST——所以 prefill 串表和 overflow 串表一样**先于**类名
表命中。顺序即语义,调换=功能回退(测试 test_classification_table 里那行
BadRequestError(PREFILL_400) 就是钉这个顺序的)。

# session/error_classifier — 规则表分类器

类名标记+消息标记两张数据表,遍历异常链;overflow 串表(业界收集)优先命中。litellm 异常按名字匹配——本文件永不 import litellm(懒加载纪律)。StepRetry 是单 step 重试上限,不是轮次上限(铁律 #14 辨析写死在 docstring)。
