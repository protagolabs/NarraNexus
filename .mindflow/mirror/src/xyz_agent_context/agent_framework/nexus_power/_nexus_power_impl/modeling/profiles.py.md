---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/modeling/profiles.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 方言按协议查,输出上限按模型查(两者的键不同,混用即 bug)

`provider` 参数是 nexus_agent 传下来的**协议名**("anthropic"/"openai"),不是厂商。
所以 `_PROFILES`(cache_style / thinking_replay / supports_arg_delta)按它匹配是对的
——anthropic 协议端点确实吃 cache_control,无论底下服务什么模型;但**一个模型能吐
多少 token 跟它说什么协议毫无关系**。

原先把 max_output_tokens 放进按协议匹配的行里,等于给「所有走 anthropic 协议的模型」
发同一个上限。NetMind 免费档那张卡正是 anthropic 协议却服务 Qwen / DeepSeek / MiMo,
于是 Qwen2.5-7B(32K 窗口)会被要求吐 128K,每个请求必炸。8_192 时代这个错误无害,
抬高上限把它引爆——**这是抬上限时最容易漏的一步**。

现在上限走 `_MODEL_LIMITS`,只按 model id 子串命中;表里没有的模型一律留在保守默认
8_192。宁可截断(有明确自救路径)也不要整条请求被拒。

## 2026-07-31 — output_budget:给输入留出位置

Anthropic 强制 `input + max_tokens <= context_window`,违反直接 400,而且那句错误
**不含任何 overflow 串表里的标记**,会被判成不可重试的 INVALID_REQUEST 杀掉 turn
(已同批给 `_OVERFLOW_MESSAGE_MARKERS` 补 "context limit"/"exceed context" 兜底)。

dev 网关实测:opus-4-8 吃下 `input_tokens=144065` + `max_tokens=128000` 仍 **200**
——144K+128K=272K 远超本模块管理的 200_000,说明它真实窗口在 1M 级,所以 Opus/Sonnet
这条钳制**永不触发**。真正需要它的是 Haiku:真实窗口就是 200K,而我们的压缩要到
150K 才触发,中间留了一段「未压缩但已超限」的带。

钳制读 `profile.output_wall`(未实测则回落到 context_window),**不读
`vendor_context_window` 原始值**——见 contracts/model mirror,那里记着字面量默认值造成
分叉、把免费档默认模型自己压到 1_024 的那次。

因此 ProviderProfile 分了两个窗口字段,不是冗余:`context_window` 是我们**选择**管理
和压缩的预算,`vendor_context_window` 是请求会 400 的**硬墙**。分开才能让钳制用真实
墙、同时不动压缩阈值(把 Opus 的 context_window 直接改成 1M 会让压缩推迟到 750K,
是本 PR 范围外的行为变更)。

# modeling/profiles — ProviderProfile 数据表

接入新 provider=加一行;字段只录实测方言,不做模型价值判断(铁律 #15)。未知→保守默认行(可跑,无优化)。claude 别名兜底进 anthropic 行。
