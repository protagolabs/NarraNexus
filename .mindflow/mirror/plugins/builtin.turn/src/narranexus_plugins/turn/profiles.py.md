---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/profiles.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— 五个内置 profile

fast/voice/job/silent 从散布各 step 的布尔开关变成**命名的策略选择**：fast → Recall=`narrative_fast`；voice →
Recall=`ephemeral`（持久化 ephemeral）；silent → Act=`silent`；job/default 全默认。`TurnProfile`（schema）继续
承载 Act 阶段读的旋钮（reasoning effort / prompt mode / framework override）——profile 决定「跑哪个策略」，
旋钮决定「怎么跑」。作为 `Contribution` 进 `turn.profiles` 注册表，插件可加自己的 profile。
