---
code_file: src/narranexus/platform/agent_framework/loop/circuit_breaker.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 输出预算耗尽不推进熔断；三道豁免收成一张表

「完全不碰熔断器」的失败类收进 `_BREAKER_EXEMPTIONS`（名字 + 谓词，按序：self-serviceable →
executor-infra → output-budget exhaustion），`breaker_exemption(error_type, error_message)` 返回命中的名字或
None，`record_failure` 只剩一处早退（debug 日志带豁免名）。各谓词旁保留原有的事故论证注释。
第三道是新增的：`error_type == OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE`（[[runtime_message]]，由 nexus_power
[[event_adapter]] 从 loop 自己的 OUTPUT_TRUNCATED 映出）——不冷却、不暂停、不动计数。成因是平台自己的预算取值 +
用户选的会思考的模型，确定性、等待不愈，冷却只会拒掉用户的下一条消息（铁律 #15）。**只认结构化 error_type、
绝不匹配 message**：message 会回显 provider/用户可控文本，短语匹配等于让调用方内容关掉熔断器。
测试：`test_output_budget_exhaustion_does_not_advance_breaker`、`test_budget_phrase_in_message_alone_does_not_exempt`
（message 含该短语但 error_type 是 `invalid_request` → 仍 COOLING）、`test_breaker_exemptions_name_each_class_and_nothing_else`。

## 2026-09-10 — 半开机制第二轮：读判断与认领分离、probe_token CAS、探测按探测语义结算

两轮预审（GitHub #117 修复分支）把上一版半开机制打回，三个根问题及本轮的定案：

**1. `should_skip` 恢复纯读，认领单独放在 `try_begin_probe`。** 上一版在
`should_skip` 里做 CAS 认领，而 bus 的 `_process_lane` 在 `should_skip` 之后还有
IM 前缀 ack、@mention 过滤 ack、限流 ack 三条"不跑 turn 就返回"的路径——群聊房间里
@mention 过滤是常态路径，3 秒一轮的 poller 几乎必然先抢到探测名额再白白扔掉，行卡在
PROBING 整个 grant 期，真人用户拿到的是误导性的 "cooling down" 帧。现在
`should_skip` 只读不写（PAUSED 且半开延迟已到 / PROBING 且 grant 已过期 → 返回
`(False, None)` 表示"窗口可能开着，继续往认领点走"），四个入口
（[[websocket.py]] fresh-run、[[message_bus_trigger]] 的 `_process_lane` 与
`_patrol_body`、[[module_poller]] Path A）各自在**真正要起 turn 的那一点**再调
`try_begin_probe(agent_id) -> (allowed, reason)`；被拒（`(False, "probing")`）与
skip 同义——bus 不 ack、WS 发 probing 帧、poller 不建 runtime。任何未来新入口都要走
这个两步契约。PR #389 的只读 `peek_skip`（回执预检用）与之并存，见其条目。

**2. CAS 键是新增列 `probe_token`，不是 `cb_status`。** 等值过滤
`cb_status=from_status` 只在写入值≠读到值时才是 CAS；stale-PROBING 自愈是
probing→probing，过滤条件对所有后来者恒真——上一版 mirror 里"CAS 本身保证只有一个
turn 通过"是**错误声明，本轮撤回**（真 MySQL 与 SQLite 实测 N 个并发全放行）。
`try_claim_probe(agent_id, from_status, expected_probe_token, grant_until)` 现在按
`probe_token`（读到的值，首次认领为 NULL → `IS NULL`）做等值过滤、写入新随机值；行写回
PAUSED/COOLING/ACTIVE 时一律置 NULL。列走 `schema_registry` additive 注册（双方言、
nullable、无回填）。并发证明用 `asyncio.gather`（顺序调用证明不了任何东西）：
`test_concurrent_claims_on_open_window_let_exactly_one_through`、
`test_concurrent_reclaims_of_stale_probing_let_exactly_one_through`，并配真 MySQL twin
`test_agent_circuit_breaker_probe_mysql.py`（aiomysql rowcount=CHANGED 行、`IS NULL`
首次认领两处方言敏感点）。把过滤里的 `probe_token` 拿掉，这四条在两种方言上都变红。

**3. 探测结果按探测语义结算（`record_failure` 看到行是 PROBING）。**
auth/quota 失败 → 沿用原 streak（+1、保留原 `paused_reason`/`failure_category`，即使这
次分类成另一个 pausing 类别）、重新 PAUSED、半开延迟翻倍、**不再重复告警 owner**（同一场
故障的延续，不是新事件）。transient/business 失败 → 什么也没证明：保持 PAUSED、streak/
类别/reason 全部不变、同样长度的延迟重新起算（`_rearm_pause_without_verdict`）——上一版
会走类别切换重置逻辑把 PAUSED 降级成 60s COOLING 循环，正是熔断器要消灭的重触发风暴；
且**故意不 +1**（铁律 #15：网络抖动不能把 owner 推向 6h 上限）。顶部两个豁免（自助类、
executor-infra）保留"不动 streak"，但 PROBING 行同样要结算回 PAUSED，否则挂到 grant 过期。

**grant 不是 turn 时长上限（铁律 #14）。** `PROBE_GRANT_SECONDS`(5min) 只界定"认领
到 run 行存在"这段窗口；stale-PROBING 重认领额外要求 `_agent_has_live_run` 为假（events
里没有心跳新鲜的 running 行——与 `run_recorder.sweep_stale_runs` 同一条活性规则，同一个
`utils.run_liveness.run_is_live` 对象，PR #394 I4 起模块级导入，不再 lazy import
`run_recorder`）。跑
几小时的探测 turn 不会被第二个探测叠上。探测永远不结算的三个口子由 `release_probe`
兜住：用户取消（[[background_run]] CANCELLED 分支）、进程死亡（[[run_recorder]]
`sweep_stale_runs` 翻 run 时顺带释放）、上述豁免。`release_probe` **只在该 agent 没有存活
run 时才归还**（同一条 `_agent_has_live_run` 活性规则；两个调用方都先把自己的 events 行
finalize/翻掉再调）——否则一个被取消的普通 turn 会把另一个仍在跑的探测 turn 的名额还回去，
放第二个探测进来。`test_release_probe_leaves_another_live_runs_claim_alone` 钉住。

**`reset_for_owner` 的 provider 维度**：`reset_for_owner(user_id, provider_id=None)`。
带 `provider_id` 时只清有效 `agent` slot 绑在该 provider 上的 agent（`agent_slots` 覆盖
优先，否则 `user_slots` 默认；2026-09-10 PR #394 I3 起经 providers 层唯一的覆盖规则
`model_identity.resolve_agent_config_slot` 判定——provider 规则，不是 identity 规则
`slot_rebinds`——本模块不再直读 slot 表）；`POST /{provider_id}/test`
成功走这条。四个重配置调用点不传 = 全量，语义是"用户变得可运行了"（slot 可能刚被改到
这个 provider 上），故意不收窄。

**其他**：CAS 写失败按"没抢到"处理（`(False, "probing")`，fail-closed，日志文案与读失败的
fail-open 区分）；`cooldown_until` 为 NULL 视为已到期（fail-safe 向探测倾斜，避免永远
探不到的行）；认领成功 / 探测成功 / 探测失败各打一条 `[agent-cb]` 日志；
`_compute_half_open_delay_seconds` 先夹指数再取幂；`_CLEAN_STATE` 含 `probe_token=None`。
`cooldown_until` 仍承载三种语义（COOLING 到期 / PAUSED 半开延迟 / PROBING grant 到期），
`probe_token` 只承担 CAS 键，不再往这列上叠第四种含义。

## 2026-09-09 — 半开（half-open）：PAUSED 不再是死胡同（GitHub #117）

**症状**：`should_skip` 原逻辑里 PAUSED 只能靠 `reset_agent`(手动) /
`reset_for_owner`(换 key 自动恢复) 解除，本身**永不按时间过期**——agent 会永久卡死在
PAUSED，即使底层凭据早已修好。

**修复**：`CbStatus` 新增 `PROBING`——半开态。`record_failure` 进入 PAUSED 分支时，
`cooldown_until` 改写成半开延迟（`_compute_half_open_delay_seconds`，首次 PAUSE 用
`PAUSE_HALF_OPEN_BASE_SECONDS`(5min)，每多一次同类失败翻倍，封顶
`PAUSE_HALF_OPEN_CAP_SECONDS`(6h)）。延迟到期后恰好一个 turn 作为探测放行；探测成功 →
ACTIVE，失败 → 重新 PAUSED、延迟翻倍。`reset_for_owner` 把 PROBING 纳入候选集。认领与
结算的细节以 2026-09-10 条为准（本条的"CAS 在 should_skip 里、按 cb_status 过滤"两点已被
撤回）。

## 2026-07-30 — `_is_out_of_credit` 改为成员判定

原本与 `SELF_SERVICEABLE_REASON_INSUFFICIENT_BALANCE` 做相等比较。免费额度用完在同日拆成
第二个 out-of-credit reason 后，相等比较让它降级成 BUSINESS —— 平台告警、永不暂停，即
**额度用尽的用户无限重试**。改为 `in OUT_OF_CREDIT_REASONS`（[[failure]]）。

这条不是新增功能，是修回归；`test_exhausted_wallet_is_quota_not_business` 抓住了它。

## 2026-07-22 — executor-infra 失败也**不进熔断器**（与自助类豁免同理）

`record_failure` 在 `classify_self_serviceable` 豁免之后新增一条：
`if error_type == EXECUTOR_INFRA_ERROR_TYPE: return`（[[runtime_message.py]]）。

原因：新的 executor-infra 收尾（[[step_3_agent_loop.py]]）对 OOM/不可达 yield 的
`ErrorMessage.error_type="infra_transient"`，若不豁免会经 background_run 的
`_last_error_type` 走到这里 → category=BUSINESS → COOLING 60s → 下一条消息被
`websocket.should_skip` 判 "cooling" 拒掉。而 surface 文案恰恰叫用户"稍后重发"——
平台侧一次抖动变成对用户的二次惩罚（铁律 #15：别成为打断源）。这是平台故障，不该
记在 agent 头上。与自助类一样：不 cool、不 pause、不动计数。

## 2026-07-14 — 确定性自助类失败**不进熔断器**（"黑盒" P1 的连带修复）

`record_failure` 顶部新增早退:`classify_self_serviceable(error_type,
error_message)` 命中(context window 太小 / 余额不足 / 模型 ID 无效)则**直接
return，不动任何熔断状态**（不 cool、不 pause、不改计数）。

**为什么**:这类错误不会靠等待自愈——只有用户改配置(换更大上下文的模型)
才好。而 `record_failure` 的既有逻辑是"除 auth/quota 外一切失败都进 COOLING
退避(60s→120s→240s…)"。"黑盒" P1 把 context-window 从 `recoverable`(不记
失败、被兜底掩盖)改成 `fatal`(记失败)后,连续几次就把 agent 冷却住了——
**用户按提示换成大模型后的那次重试反而被冷却挡在门外**(实测复现:换到
deepseek-v4-pro 仍报 "cooling down, try again shortly")。这违反铁律
#14/#15(平台不能成为中断源)。且这类是**瞬时拒绝的 400**,不是"把挣扎中的
provider 打垮"的 DoS 风险,熔断器本就不该管。

早退用 `classify_self_serviceable`(双通道)而非只认 `config_actionable`
标记,因为 message_bus 路径只传 `str(e)` 原文——两条路径都能命中。留下的
无关既有 streak 不受影响(早退在任何读写之前)。

# loop/circuit_breaker.py — 实时层 Agent 熔断器（核心服务）

## 为什么存在

实时对话层没有熔断：一个持续失败的 Agent（401、余额耗尽、模型不可用）会被 WebSocket
新 run、message bus 轮询、module poller 一遍遍重触发。本服务复用 Job 层的分类/退避，
在每个实时触发入口设"跳过闸门"，并在 turn 结束时记账。

## 2026-09-10（PR #389 I3）— AUTH 判定改用宽松的 `is_auth_like_error`

`forbidden` 回到「只影响分类」的作用域（见 [[failure.py]]）；严格版留给控制流。顺序不变：
TRANSIENT 仍先于 AUTH。

## 2026-09-09（PR #389 review C1）— 新增只读入口 `peek_skip(agent_id, *, db)`

bus 发送工具的回执预检（[[_message_bus_mcp_tools]] `_book_receipt`）只想知道「收件方现在
跑不跑」，绝不能替收件方消耗唯一的半开探针。`peek_skip` 只读原始行（不走实体：实体的枚举
会**拒绝**本构建不认识的状态，而「未知状态」恰是这里要判成 held 的那种）：ACTIVE → 不 held；
COOLING 未到期 → held、已到期 → 不 held（下一真 turn 会放行，读侧不改状态）；PAUSED →
`paused:<reason>`（含半开延迟已过的行——预检不是 turn，不替 turn 去试探针）；其余任何状态
（含 `probing`）→ held、原因=状态名。fail-open 与 `should_skip` 一致。

与 2026-09-10 条的关系（两分支合并后的口径）：`should_skip` 现在也是纯读，探针只由
`try_begin_probe` 在 turn 起点消耗；`peek_skip` 与 `should_skip` 的唯一差别是对"半开延迟
已过的 PAUSED 行"的读法——前者读作 held（预检不会起 turn），后者返回 `(False, None)` 让
调用方去 `try_begin_probe`。锁：`test_agent_circuit_breaker.py::test_peek_skip_*`、
`test_delivery_receipts.py::test_pre_flight_never_calls_the_turn_gate`（monkeypatch
`should_skip` 为必炸）。

## 2026-09-09 — AUTH 判定里的 `"forbidden"` 补丁删掉，统一回 `is_credential_error`

`classify_agent_error` 第 ③ 步原本在 `is_credential_error` 之外再补一句
`"forbidden" in msg.lower()`，因为旧 marker 表的 `" 403"/"(403"` 要求数字前有分隔符，
顶格的 "403 Forbidden" 漏网。[[failure.py]] 今日改成锚定正则（403 按整数匹配、
`forbidden` 进表、裸 "provider" 出表），这句补丁成了第二份口径，删掉。行为由
`test_agent_circuit_breaker.py` 的 "HTTP 403 Forbidden" / "403 Forbidden" 两条用例钉住；
"provider temporarily unavailable" 仍靠 TRANSIENT 先判，但即使去掉那层，它也不再是 AUTH。

## 核心行为（分而治之）

`classify_agent_error` 是**四分类**，顺序刻意：① QUOTA（error_type 精确匹配）② TRANSIENT
（**正面识别** provider 侧：429/5xx/超时/网络/overloaded；放在 auth 之前，让一条恰好带
凭据词的瞬时错误——"authentication service timed out"——落 TRANSIENT 而不是 AUTH）
③ AUTH（凭证死）④ **BUSINESS = 真正的残余桶**：我们自己的 pipeline bug、永久客户端错
（context 超长 / 模型 404 / content policy）、或认不出的。

`record_failure`：每次失败 → 分类 → 同类连击 +1（类别变则重置为 1）→ 写 COOLING +
退避 `cooldown_until`。仅 `category ∈ {auth,quota}` 且连击 ≥ `AUTH_QUOTA_PAUSE_THRESHOLD(3)`
才 PAUSE + 告警 owner。transient/business **永不 PAUSE**（铁律 #15）。连续第
`SUSTAINED_FAILURE_ALERT_THRESHOLD(5)` 次时按**谁能处理**分流告警：TRANSIENT（provider
侧，用户能判断）→ 给 **owner** 一条中性知会（绝不说"换模型"）；BUSINESS（我们的 bug，
owner 修不了）→ **只报平台方**（内部审计 + loud log），**绝不发 owner**。每段连击一次
（成功即清零）。

`record_success` 清零；`should_skip` 是**纯读、fail-open** 的预过滤闸门（读错→放行）：
PAUSED 且半开延迟未到→skip，已到→`(False, None)` 让调用方走向认领；PROBING 且 grant 未过
→skip("probing")，已过→同样放行去认领；COOLING 且 `cooldown_until>now`→skip，到期→惰性
放行。`try_begin_probe` 是唯一写 PROBING 的地方（`probe_token` CAS），只在 turn 真正要
起的那一点调用。`reset_agent`（手动）/`reset_for_owner`（换 key 自动恢复，清
PAUSED/PROBING + auth/quota 的 cooling 连击，不动 transient 冷却）。`release_probe` 供
取消/丢失的探测 turn 归还名额。

## 上下游关系

被 `agent_runtime/background_run._record_circuit_breaker`（记账 + 取消时 `release_probe`）、
`agent_runtime/run_recorder.sweep_stale_runs`（丢失 run 时 `release_probe`）、
`backend/routes/websocket.py` + `message_bus/message_bus_trigger.py` +
`services/module_poller.py`（`should_skip` 读闸门 + `try_begin_probe` 认领）、
`backend/routes/providers.py`（reset_for_owner 自动恢复）、`backend/routes/agents/circuit_breaker.py`
（reset_agent 手动）调用。分类复用 `llm.failure.is_credential_error` +
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
