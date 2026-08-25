---
code_file: src/xyz_agent_context/services/background_llm_alerts.py
last_verified: 2026-08-24
stub: false
---

## 2026-08-24 — 新增 `alert_ingress_breaker_tripped`

与本文件里其他告警都不同：它**不是**在报故障。ingress 熔断器跳闸时平台完全
按设计工作。它报的是**可见性**——跳闸意味着一整个对话安静下去，而
「用户无法解释的安静」本身就是一次事故。8/14 那 70 小时之所以能跑，正是
因为关于那个对话的任何信息都没有到达人类。

沿用本文件既有的两层结构（`ServiceAuditor` 审计行 + `InboxRepository` 收件箱
通知 + 30 分钟冷却）。三点差异：

- **按会话去重**，不是按 agent：两个不同的失控对端是用户要分别知道的两件事，
  而同一个对端连升四档是一个故事。
- **措辞不评判对面的软件**（铁律 #15）：只陈述观察到什么、我们做了什么，
  判断留给用户。
- `verdict` 按结构取（鸭子类型），不 import `IngressVerdict`——`services/`
  不该依赖 `channel/`。

同期：[[_entity_updater.py]] 终于接上了 `alert_background_llm_failure`。该
函数的 docstring 从写下那天起就把 `entity_summary` 列为预期 source，坑挖好
了一直没埋线。

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
