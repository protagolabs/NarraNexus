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

## 2026-09-10（PR #394 review 第三轮）— 探测身份随 turn 走；只有能结算的入口才认领

上一轮把认领下移到了 turn 起点，但 #394 预审指出两个根问题，本轮定案如下（上一轮条目中与
此冲突的说法已一并改写，以本条为准）。

**1. 认领有身份：`probe_token` 交给赢得认领的 turn，结算一律按 token CAS（I1）。**
`try_begin_probe(agent_id, db=None, *, prior=None) -> TurnAdmission(allowed, reason,
probe_token)`；赢了才有 `probe_token`，turn 必须把它带到结算。`record_success` /
`record_failure` 新增关键字参数 `probe_token`：行是 PROBING 且 token 匹配 → 按探测语义结算，
写入走仓储的 `settle_probe(agent_id, token, updates)`（过滤
`cb_status=probing AND probe_token=token`，MySQL CHANGED 行计数下也可靠，因为 token 列必变）；
行是 PROBING 但 token 不匹配（认领之前就在跑的长 run、从未认领的入口）→ **完全不动**，留给
认领者结算；其余状态照常记普通 streak。于是「一个从没认领的 turn 替探测下结论」（旧的
`record_success` 只看 `cb_status==PROBING` 就清 ACTIVE、`record_failure` 的 `was_probing`
分支把别人的失败当探测结论）不再可能。`release_probe(agent_id, probe_token, db=None)` 同样
按 token CAS，不匹配即 no-op、可幂等——所以每个被放行的 turn 可以在结束处无条件调用。

旧的「该 agent 有没有存活 run」代理判定（`_agent_has_live_run`）已删除：它会把行焊死在
PROBING（长 run A 在跑、探测 C 被取消 → release 因 A 存活而 no-op → grant 过期后又因 A 存活
拒绝重认领）。崩溃窗口（认领者死了、token 随进程消失）的兜底改为 `_claimant_may_be_live`：
只有 **在认领之后才开始**（`started_at >= probe_claimed_at - 5s` 时钟容差）且心跳新鲜的
running 行才可能是认领者。新增列 `probe_claimed_at`（与 `probe_token` 同写同清，见
[[schema_registry]]）；老行没有该列值时退回「任一存活 run」这个保守答案。它被两处使用：
`try_begin_probe` 的过期 grant 重认领、`release_orphaned_probe`（[[run_recorder]]
`sweep_stale_runs` 翻掉丢失 run 后调用）。

**2. 每个认领入口都有结算；不能结算的入口不认领（C1）。** bus lane / patrol 不经
`BackgroundRun`，上一轮只认领不结算：死凭据每个 grant 周期（5min）重跑一个真 turn、
streak 不涨、延迟永不翻倍；修好的凭据也回不到 ACTIVE。现在：
- 共享结算接缝 `settle_probe(agent_id, probe_token, *, succeeded, error_type,
  error_message)`：`True`→`record_success`，`False`→`record_failure`，`None`→
  `release_probe`，全部带同一个 token——与 WS 路径同一组函数。只在 token 仍是活认领时生效，
  无 token 直接返回：这些入口**只记探测结论，不记普通 streak**（与 #117 之前一致）。
- [[client]] `InProcessAgentRuntimeClient.run_and_collect(probe_token=...)` 在 events 行
  终态之后调用它：`RunCollection.is_fatal` → 失败，错误类型/文案取 runtime 自己的 error
  帧（`classify_agent_error` 认得的词表；bus 的 `str(e)` 会被分成 transient、原样重挂延迟，
  等于换条路回到同一个循环）；抛异常 → 失败（异常类型名）；`CancelledByUser` → 归还。
- [[message_bus_trigger]] lane 与 patrol 把 token 一路传到 `run_and_collect`，并在各自出口
  无条件 `release_probe`（lane 用 `try/finally`，patrol 挂在 `AsyncExitStack`）兜住「turn
  还没到结算就抛了/被取消」；已结算时 CAS 不匹配，no-op。
- [[module_poller]] Path A 的 `_execute_callback_instance` 吞掉所有失败、没有结果信号，
  **改为只用 `peek_skip`**：PAUSED（含半开窗口已开）/PROBING 一律不跑、不认领。
- [[openai_compat]] 是第五个起真 turn 的入口，原来零闸门却由 `BackgroundRun` 记账（I5）：
  补上同样两步，放在 run-job / managed deny / 群聊静默入库这些「不起 turn」分支之后，
  拒绝时回 OpenAI 形状的 503（`type=agent_circuit_open`、`code=<reason>`），赢得的 token
  交给 `BackgroundRun(probe_token=...)`。
- [[websocket.py]] 同样把 token 交给 `BackgroundRun`；认领后、`bg` 建出来之前抛异常时，外层
  `finally` 按 token 归还（`bg` 已存在则由 run 自己结算，此时归还会在活探测下误重挂）。

**入口契约（新入口照此办理）**：`verdict = should_skip(...)` → 所有「不起 turn」分支 →
`admission = try_begin_probe(..., prior=verdict)` → 把 `admission.probe_token` 交给 turn 的
结算（`BackgroundRun` 或 `run_and_collect`），出口兜底 `release_probe(token)`。给不出结果
信号的入口只能用 `peek_skip`，不许认领。扫描口径：`git grep -n "should_skip\|try_begin_probe\|
peek_skip\|BackgroundRun(\|run_and_collect("`（按「起 turn 的构造点」扫，而不是按已有闸门扫）。

**3. 其余。** `should_skip` 返回 `GateVerdict(skip, reason, row, row_known)`，入口把它作为
`prior=` 交给 `try_begin_probe`，普通 turn 只读一次（M1；bus lane 的读与认领之间隔着锁和
信号量，重用旧读是安全的：认领是 token CAS，旧 PAUSED 读只会输；旧 ACTIVE 读放行与 #117
之前 bus 只有顶部一次读的行为相同；patrol 的派发读已隔一个周期，认领时重读）。
`record_failure` 的两条豁免（自助类 / executor-infra）现在在任何读之前判定，普通 turn 的
豁免失败零 DB 往返，只有持 token 者才读并归还（M2）。`reset_for_owner(provider_id=...)` 的
绑定判定改用 providers 层唯一的覆盖规则 `model_identity.resolve_agent_config_slot`（I3，
provider 规则：`agent_slots` 行有非空 `provider_id` 即胜出，**不是** identity 规则
`slot_rebinds`），本模块不再直读 slot 表。活性规则从 `utils.run_liveness` 模块级导入（I4），
与 `sweep_stale_runs` 用同一个 `run_is_live` 对象。拒绝文案抽成
`describe_skip_reason(reason)`，WS 帧与 openai_compat 共用。

锁住这些的测试：`test_agent_circuit_breaker.py`（`test_release_probe_settles_only_its_own_claim`、
`test_a_long_run_that_predates_the_claim_cannot_weld_the_probe`、
`test_a_turn_that_did_not_claim_cannot_settle_the_probe`、
`test_a_probe_verdict_does_not_land_on_a_row_reset_meanwhile`、
`test_settle_probe_*`、`test_claim_reuses_the_gate_read`、
`test_exempt_failure_of_an_ordinary_turn_reads_nothing`）、真 MySQL twin 的
`test_settlement_cas_wins_only_with_the_live_token` /
`test_claimant_liveness_compares_mysql_datetimes`、
`tests/agent_runtime/test_client_probe_settlement.py`（死凭据探测 streak+1 且下次延迟翻倍）。

## 2026-09-10 — 半开机制第二轮：读判断与认领分离、probe_token CAS、探测按探测语义结算

两轮预审（GitHub #117 修复分支）把上一版半开机制打回，三个根问题及该轮的定案（认领身份、
结算入口与活性兜底已被上面的第三轮条目取代）：

**1. `should_skip` 恢复纯读，认领单独放在 `try_begin_probe`。** 上一版在
`should_skip` 里做 CAS 认领，而 bus 的 `_process_lane` 在 `should_skip` 之后还有
IM 前缀 ack、@mention 过滤 ack、限流 ack 三条"不跑 turn 就返回"的路径——群聊房间里
@mention 过滤是常态路径，3 秒一轮的 poller 几乎必然先抢到探测名额再白白扔掉。现在
`should_skip` 只读不写，认领只在真正要起 turn 的那一点做；被拒与 skip 同义——bus 不 ack、
WS 发 probing 帧。PR #389 的只读 `peek_skip`（回执预检用）与之并存，见其条目。

**2. CAS 键是新增列 `probe_token`，不是 `cb_status`。** 等值过滤
`cb_status=from_status` 只在写入值≠读到值时才是 CAS；stale-PROBING 自愈是
probing→probing，过滤条件对所有后来者恒真（真 MySQL 与 SQLite 实测 N 个并发全放行）。
`try_claim_probe(agent_id, from_status, expected_probe_token, grant_until)` 按
`probe_token`（读到的值，首次认领为 NULL → `IS NULL`）做等值过滤、写入新随机值；行写回
PAUSED/COOLING/ACTIVE 时一律置 NULL。并发证明用 `asyncio.gather`：
`test_concurrent_claims_on_open_window_let_exactly_one_through`、
`test_concurrent_reclaims_of_stale_probing_let_exactly_one_through`，并配真 MySQL twin
`test_agent_circuit_breaker_probe_mysql.py`。

**3. 探测结果按探测语义结算。** auth/quota 失败 → 沿用原 streak（+1、保留原
`paused_reason`/`failure_category`）、重新 PAUSED、半开延迟翻倍、不再重复告警 owner。
transient/business 失败 → 什么也没证明：保持 PAUSED、streak/类别/reason 全部不变、同样
长度的延迟重新起算（`_rearm_pause_without_verdict`），故意不 +1（铁律 #15）。两个豁免
（自助类、executor-infra）保留"不动 streak"，但持有中的探测同样要结算回 PAUSED。

**grant 不是 turn 时长上限（铁律 #14）。** `PROBE_GRANT_SECONDS`(5min) 只界定崩溃窗口；
过期 grant 的重认领还要求认领者不再存活（第三轮起为 `_claimant_may_be_live`）。

**其他**：CAS 写失败按"没抢到"处理（fail-closed，日志文案与读失败的 fail-open 区分）；
`cooldown_until` 为 NULL 视为已到期；`_compute_half_open_delay_seconds` 先夹指数再取幂。
`cooldown_until` 仍承载三种语义（COOLING 到期 / PAUSED 半开延迟 / PROBING grant 到期）。

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
调用方去 `try_begin_probe`（现为 `GateVerdict(skip=False)`）。锁：`test_agent_circuit_breaker.py::test_peek_skip_*`、
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

`record_success` 清零（PROBING 时只有持 token 的认领者能清）；`should_skip` 是**纯读、
fail-open** 的预过滤闸门，返回 `GateVerdict`（读错→放行）：PAUSED 且半开延迟未到→skip，
已到→不 skip 让调用方走向认领；PROBING 且 grant 未过→skip("probing")，已过→同样放行去
认领；COOLING 且 `cooldown_until>now`→skip，到期→惰性放行。`try_begin_probe` 是唯一写
PROBING 的地方（`probe_token` CAS），只在 turn 真正要起的那一点调用，返回 `TurnAdmission`。
`reset_agent`（手动）/`reset_for_owner`（换 key 自动恢复，清 PAUSED/PROBING + auth/quota
的 cooling 连击，不动 transient 冷却）。`release_probe(token)` 供取消/没到结算的探测 turn
归还名额，`release_orphaned_probe` 供丢失 run 的清扫兜底，`settle_probe` 是不经
`BackgroundRun` 的触发路径的结算接缝。

## 上下游关系

被 `agent_runtime/background_run._record_circuit_breaker`（带 token 记账 + 取消时
`release_probe`）、`agent_runtime/client.run_and_collect`（`settle_probe`）、
`agent_runtime/run_recorder.sweep_stale_runs`（丢失 run 时 `release_orphaned_probe`）、
`backend/routes/websocket.py` + `backend/routes/openai_compat.py` +
`message_bus/message_bus_trigger.py`（`should_skip` 读闸门 + `try_begin_probe` 认领）、
`services/module_poller.py`（只用 `peek_skip`）、
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
- 全部写操作在调用方（background_run / runtime client）以 best-effort 包裹：熔断器是观察者，绝不能弄坏被
  观察的 turn 收尾。
