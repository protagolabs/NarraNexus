---
code_file: src/xyz_agent_context/agent_framework/nexus_power/contracts/model.py
last_verified: 2026-07-29
stub: false
---
# contracts/model — 方言是数据不是代码

ProviderProfile 一行数据描述一家 provider(cache 方言/窗口/参数增量能力位),新 provider=加行,未知走保守默认(任何用户模型可跑,铁律 #15)。ModelEventKind 是封闭 Literal(R3):翻译层自己的词汇,构造期校验,拼错当场炸。content_index 对齐分片(pi 纪律:块事件不保证连续)。
