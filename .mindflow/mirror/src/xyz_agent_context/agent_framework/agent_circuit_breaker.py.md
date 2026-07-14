---
code_file: src/xyz_agent_context/agent_framework/agent_circuit_breaker.py
last_verified: 2026-07-13
stub: false
---
# agent_circuit_breaker.py — 实时层 Agent 熔断器（核心服务）

## 为什么存在

实时对话层没有熔断：一个持续失败的 Agent（401、余额耗尽、模型不可用）会被 WebSocket
新 run、message bus 轮询、module poller 一遍遍重触发。本服务复用 Job 层的分类/退避，
在每个实时触发入口设"跳过闸门"，并在 turn 结束时记账。

## 核心行为（分而治之）

`classify_agent_error` 是**四分类**，顺序刻意：① QUOTA（error_type 精确匹配）② TRANSIENT
（**正面识别** provider 侧：429/5xx/超时/网络/overloaded；放在 auth 之前，避免 "provider
temporarily unavailable" 被 `is_credential_error` 的宽泛 "provider" 子串误扫进 auth）
③ AUTH（凭证死）④ **BUSINESS = 真正的残余桶**：我们自己的 pipeline bug、永久客户端错
（context 超长 / 模型 404 / content policy）、或认不出的。

`record_failure`：每次失败 → 分类 → 同类连击 +1（类别变则重置为 1）→ 写 COOLING +
退避 `cooldown_until`。仅 `category ∈ {auth,quota}` 且连击 ≥ `AUTH_QUOTA_PAUSE_THRESHOLD(3)`
才 PAUSE + 告警 owner。transient/business **永不 PAUSE**（铁律 #15）。连续第
`SUSTAINED_FAILURE_ALERT_THRESHOLD(5)` 次时按**谁能处理**分流告警：TRANSIENT（provider
侧，用户能判断）→ 给 **owner** 一条中性知会（绝不说"换模型"）；BUSINESS（我们的 bug，
owner 修不了）→ **只报平台方**（内部审计 + loud log），**绝不发 owner**。每段连击一次
（成功即清零）。

`record_success` 清零；`should_skip` 是**fail-open** 的读闸门（读错→放行，绝不因熔断器
故障挡住健康 turn）：PAUSED→skip，COOLING 且 `cooldown_until>now`→skip，冷却到期→惰性
放行。`reset_agent`（手动）/`reset_for_owner`（换 key 自动恢复，只清 auth/quota 的
paused + auth/quota 的 cooling 连击，不动 transient 冷却）。

## 上下游关系

被 `agent_runtime/background_run._record_circuit_breaker`（记账）、`backend/routes/websocket.py`
+ `message_bus/message_bus_trigger.py` + `services/module_poller.py`（should_skip 闸门）、
`backend/routes/providers.py`（reset_for_owner 自动恢复）、`backend/routes/agents_circuit_breaker.py`
（reset_agent 手动）调用。分类复用 `llm_failure.is_credential_error` +
`response_processor._is_auth_failure`；告警复用 `services/background_llm_alerts`。

## 设计决策 / Gotcha

- `_NO_QUOTA_ERROR_TYPES` **复制**自 job_trigger（不 import job 模块——模块相互独立，
  铁律 #3）。这也是未来"Executor 余额不足"的接入点。
- 分类顺序：先按 error_type 精确匹 quota（避开 quota-vs-限流的子串陷阱），再 auth，其余
  transient。
- 铁律 #14/#15：只对**已结束且失败**的 turn 记账，闸门只挡**新 turn 的调度**，绝不 kill
  在飞 loop、不设 loop 长度上限。
- 全部写操作在调用方（background_run）以 best-effort 包裹：熔断器是观察者，绝不能弄坏被
  观察的 turn 收尾。
