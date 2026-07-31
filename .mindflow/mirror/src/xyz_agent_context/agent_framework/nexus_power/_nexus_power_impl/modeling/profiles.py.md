---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/modeling/profiles.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — max_output_tokens 一律取厂商上限;haiku 单独一行且必须排在 anthropic 前面

上限压低**不省钱只致命**:它把工具参数从 JSON 中间切断,模型看不见切口也无法
自救(2026-07-30 事故,8_192 上限下一个 write_file 被反复重发成死循环)。深度和
成本是调用方的旋钮(铁律 #15),不是这张表的。Opus/Sonnet=128_000,Haiku=64_000,
均为官方最大值。

haiku 行**必须排在 anthropic 之前**:解析是「按 _PROFILES 顺序遍历,provider 和
model 任一子串命中即返回」,provider 字符串本身就是 "anthropic",排在后面的 haiku
行永远轮不上,每个 Haiku 请求都会带着双倍上限上去。dev 网关实测钉死:
opus-4-8@128000→200、sonnet-4-6@128000→200、haiku-4-5@64000→200、
haiku-4-5@128000→**400**。

其余 provider(openai/deepseek/qwen)仍吃 8_192 默认值,是**已知未修**——它们的
官方上限没实测过,照搬 128K 会换来一批 400。要修就逐个实测后加行。

# modeling/profiles — ProviderProfile 数据表

接入新 provider=加一行;字段只录实测方言,不做模型价值判断(铁律 #15)。未知→保守默认行(可跑,无优化)。claude 别名兜底进 anthropic 行。
