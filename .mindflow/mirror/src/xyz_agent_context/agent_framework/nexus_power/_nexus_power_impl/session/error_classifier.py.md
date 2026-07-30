---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/session/error_classifier.py
last_verified: 2026-07-29
stub: false
---
# session/error_classifier — 规则表分类器

类名标记+消息标记两张数据表,遍历异常链;overflow 串表(业界收集)优先命中。litellm 异常按名字匹配——本文件永不 import litellm(懒加载纪律)。StepRetry 是单 step 重试上限,不是轮次上限(铁律 #14 辨析写死在 docstring)。
