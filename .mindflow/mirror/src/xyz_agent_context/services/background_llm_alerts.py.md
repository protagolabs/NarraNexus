---
code_file: src/xyz_agent_context/services/background_llm_alerts.py
last_verified: 2026-08-25
stub: false
---

## 2026-08-25 — source 标签枚举补全（行为未变）

`alert_background_llm_failure` 的 docstring 原本只列了三个 source，而
[[_entity_updater.py]] 这次接线新增了四个（`entity_dedup` /
`entity_extraction` / `description_compression` / `persona_inference`）。
运维照 docstring 那张清单去查会漏掉它们。

**补充（同轮 review）**：旧 docstring 列的三个 source 里，`memory_extraction`
**从来没有任何代码发出过**——全仓只有那一行 docstring 命中。一个查出零行的
标签没法让运维分辨「这条链健康」还是「标签被改名了」，正是这份清单想消灭的
那种摩擦，已删除并注明原因。清单旁边加了一句纪律：**新增 source 必须同一个
commit 同步这里**，否则下一个接线的人还会漏（这一轮就漏了四个）。

同时写明：**同一条因果链有两个 audit service 名**——LLM 失败走
`background_llm`，DB 写入失败走 `social_network_memory`（不是 LLM 失败、
也不打扰 owner）。「记忆为什么停更」的排查要覆盖两个名字。

本文件代码行为未变，仅补文档。

## 2026-07-13 — Agent 实时层熔断器接入

新增熔断器告警面，按**谁能处理**分流：
- `alert_agent_paused`（PAUSE 时——audit 恒写 + owner inbox，auth/quota 都发，按 (agent_id,reason) 30min 去重，文案区分 auth/quota）。
- `alert_agent_transient_streak`（provider 侧持续失败——audit + **owner** 中性知会："还在重试、这是原始错误、你自己判断",绝不说"换模型"，按 (agent_id,"transient") 去重）。
- `audit_agent_internal_streak`（BUSINESS 类：我们的 bug/永久错——**只**写内部审计 + loud `[agent-cb][PLATFORM]` error log，**绝不**发 owner，因为 owner 修不了我们的缺陷）。

复用既有 redact_secrets / ServiceAuditor / InboxRepository / 冷却 map。分流的意义：该用户
修的找用户，我们该修的找我们，谁都别被无关告警骚扰。

# background_llm_alerts.py — 后台 LLM 失败告警

## 为什么存在

脱离任务（narrative updater、Step-5 entity/memory hooks）里的 LLM 失败以前是纯静默：
`logger.exception` 后 `return None`。2026-07 事故里平台 key 过期，这些路径 401 两周
无 owner 可见信号、也无可 SQL 的痕迹，长记忆无声退化。事故教训 #3（别吞异常）、#4
（L2 健康）、#5（DB 审计）都指向同一结论：后台 LLM 失败必须留可查记录，且当它是 owner
能修的凭据问题时，发一条去重的 owner 通知。

`alert_background_llm_failure(...)` 分两级（刻意）：
- **每次失败** → 写一条 `service_audit` error 行。永远开、便宜，运维几周后仍能
  `SELECT` 出"最近 N 天多少次后台 LLM 失败"，即使日志已轮转。
- **仅凭据类失败** → 写 owner inbox 通知（脱敏 + 冷却去重）。瞬时抖动（超时/5xx）
  owner 修不了，不进 inbox，避免告警疲劳。

`source` 短标签（narrative_update / entity_summary / memory_extraction /
post_turn_hooks）进审计 detail 和通知标题。冷却 map 在进程内（重启即清），与 message
bus 的失败通知同款权衡。函数**永不抛**——观察者不得破坏被观察者。

## 上游

被 narrative updater 的 `_async_llm_update` 和 AgentRuntime 的 `_run_hooks_background`
调用。复用 `agent_framework/llm/failure`（分类+脱敏）、`InboxRepository`（owner 通知）、
`ServiceAuditor`（审计）。
