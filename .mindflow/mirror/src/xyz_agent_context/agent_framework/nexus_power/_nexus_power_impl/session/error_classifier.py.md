---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/session/error_classifier.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — overflow 串表补 Anthropic 的「输入+输出」墙

`input length and \`max_tokens\` exceed context limit: 154321 + 128000 > 200000`
和串表里既有的说法**一个词都不重合**,原先直落 BadRequestError 行 → 不可重试的
INVALID_REQUEST → turn 当场死。补 "context limit" / "exceed context" 后至少退化成
压缩重试。真正该让我们不撞这堵墙的是 profiles 的 output_budget 钳制,这两行是它下面
的网。

## 2026-07-31 — prefill 拒绝串表插在类名表之前

网关某些后端拒绝「以 assistant 结尾」的对话(400 "does not support assistant
message prefill")。包装它的是普通 litellm BadRequestError,若走类名表会被判成
死路一条的 INVALID_REQUEST——所以 prefill 串表和 overflow 串表一样**先于**类名
表命中。顺序即语义,调换=功能回退(测试 test_classification_table 里那行
BadRequestError(PREFILL_400) 就是钉这个顺序的)。

# session/error_classifier — 规则表分类器

类名标记+消息标记两张数据表,遍历异常链;overflow 串表(业界收集)优先命中。litellm 异常按名字匹配——本文件永不 import litellm(懒加载纪律)。StepRetry 是单 step 重试上限,不是轮次上限(铁律 #14 辨析写死在 docstring)。
