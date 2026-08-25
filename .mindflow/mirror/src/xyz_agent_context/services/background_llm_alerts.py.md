---
code_file: src/xyz_agent_context/services/background_llm_alerts.py
last_verified: 2026-08-25
stub: false
---

## 2026-08-25（下午）— ingress 通知层整个拆出本文件

**本页下方两条 2026-08-25 的 ingress 条目描述的代码已不在这个文件里。**
`alert_ingress_breaker_tripped` / `_send_ingress_digest` /
`_ingress_quota_spend` / `INGRESS_NOTICE_QUOTA_PER_AGENT` 全部删除，本文件
回到只承载 LLM 后台失败与 agent 熔断两类告警。

范围决定（PR#358 第三轮 review 后，Owner 拍板）：ingress 熔断的 P1 目标是
**止血**，无损证据链由 `channel_trigger_audit` 的三个事件承担，收件箱通知
是锦上添花。而这一层在两轮 review 里稳定产出 6 条 finding（配额、汇总、
文案、去重槽），是这个 PR 不收敛的主因之一。拆出去单独做透。

**缺口是真实的**：owner 现在没有关于 ingress 熔断的主动推送，只能从审计表
或 `/healthz` 发现。重做时的六个坑记在
`reference/self_notebook/todo/2026-08-25-ingress-breaker-owner-notice.md`。

## 2026-08-25 — ingress 通知：改掉假承诺 + 加基数上限（review I2/I5）

**I2 · 文案说了假话。** 原文告诉 owner「这个会话的消息会被**记录**下来，
只是不处理」。实际上闸门在 inbox 写入**之前**返回，被 drop 的消息不进
inbox、不进 message 表；唯一留下的是一条 `channel_trigger_audit` 行，
`details` 里只有 session_key / tier / 计数 / 比率，**不含内容**。owner 读到
那句话会以为「等 24 小时回去翻就行」，于是选择不干预，24 小时后什么都翻
不到。这是 owner 面向的承诺，不是内部注释。改成实话：不处理、不留内容、
审计里只有条数与时间。

**I5 · 通知基数无上限。** 去重键是 per-session 的（有意：两个不同的失控
对端是两件事），但 session 基数由**外部输入**决定——`chat_id` / `sender_id`
都来自平台侧。任何允许陌生人私聊的渠道上，N 个发送者各自刷满阈值就是 N 条
收件箱通知。告警疲劳一旦形成不可逆，而这个通道正是本 PR 用来解决
「70 小时没人知道」的唯一人肉出口。

加 per-agent 配额（`INGRESS_NOTICE_QUOTA_PER_AGENT`），超出后只记 warning。
**配额只限制 inbox 通知，绝不限制 `ServiceAuditor` 那条审计写入**——审计面
必须无损（教训 #5），被配给的是人肉通道不是证据链。

`_ingress_notice_quota` 和 `_notify_cooldown` 里 `ingress:` 那一类 key 都做
惰性清扫：别用一个新的无界 dict 去修一个无界 dict。

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
