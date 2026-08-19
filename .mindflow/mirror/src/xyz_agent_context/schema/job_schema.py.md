---
code_file: src/xyz_agent_context/schema/job_schema.py
last_verified: 2026-08-14
stub: false
---

## 2026-08-14 — `JobOrigin` + 两个 origin 字段

job 记住它是在哪儿被要求的。此前 job 只记得**做什么**，忘了**在哪被问**——于是
在团队房里当着四个人要的「明早提醒我们」，投进了 owner 的私聊，问的那个房间再
没收到过回音。

`JobOrigin` 刻意是**小的闭集**而不是「任意 WorkingSource」：每个值都需要真实存在
的投递代码，一个能记录却投不出去的来源比不记录更糟——执行时会把答案路由进一个
静默什么都不做的分支。

`MESSAGE_BUS` 目前特指**团队房**。peer DM 不在内：agent 对 agent 的频道没有人类
读者，往那儿投的报告等于没人看见，owner 私聊才是它诚实的归宿。

## 2026-08-10 (PR-8b r2) — `JobUpdateFields`：job_update 可变字段的单一来源

新增 `JobUpdateFields`(9 个 optional 字段：title/description/payload/guidance_text/
trigger_config/job_type/next_run_time/status/related_entity_id)。存在理由：job_update
的字段清单原本手抄 4 份——`update_job_from_args` keyword 签名、MCP 工具 `fields` 字面量、
前端 `JobUpdateBody`、seam `JobUpdateSeamBody`。schema 层两份 body 现都 derive 自本类
（前端加 `agent_id`，seam 加 `extra="forbid"`），字段只声明一次。**parity 承重**：seam
是 HttpStore 写路径，pydantic 默认 `extra="ignore"` 会让「加字段漏改 body」在云上静默丢字段
（success=True 但少写）而本地 DirectStore 却写入=seam byte-parity 要防的分叉；forbid 让漂移
以 422 响亮失败。None=不改语义见 [[_job_writes]] `update_job_from_args`。放 schema 包因它是
工具契约(agent-side)，两个 backend 路由 import 它属允许方向(backend→agent，铁律 #21)。

## 2026-06-01 — resilience states + backoff fields (batch ②)

`JobStatus` gains three values, modelling "non-terminal, can't run now, blocked
on a specific guard" (recovery = that guard re-passing, all arbitrated in
JobTrigger): `COOLING` (transient-failure backoff; time-recovered), `BLOCKED`
(unmet job dependencies), `BLOCKED_FAILED` (a prerequisite FAILED and policy is
"block"). `JobModel` gains `consecutive_failure_count`, `cooldown_until`,
`paused_reason` (no_quota / repeated_failure / dependency_failed / user),
`paused_at`. All additive (enum strings + nullable/default columns) → 铁律 #6
safe. See `job_trigger.py` for the state machine and
`2026-06-01-job-scheduler-resilience-design.md`.

## 2026-05-22 — JobStatus.PAUSED_NO_QUOTA (#6)

New status value `paused_no_quota`: a recurring/ongoing job whose run failed
because the owner's free-tier quota is exhausted (and no own provider) is parked
here instead of being rescheduled (which caused the every-interval infinite-loop
re-fire). Distinct from the reserved generic `PAUSED` so the frontend can label
it "No quota" and JobTrigger's recheck can target it for auto-resume. Additive
enum value (string) → safe per 铁律 #6. Mirrors: `job_trigger.py` (pause/resume),
`JobsPanel.tsx` + `api.ts` (frontend label).

# job_schema.py

## Why it exists

Background tasks (Jobs) are a first-class concept in NexusAgent — they allow the agent to do work on the user's behalf on a schedule or with a delay, without blocking real-time conversation. This file defines the entire data contract for that system: how jobs are described (`JobModel`), how triggers are configured (`TriggerConfig`), and how the LLM reports back what happened after each execution (`JobExecutionResult`, `OngoingExecutionResult`).

**v2 timezone protocol (2026-04-21):** `TriggerConfig` now enforces a `timezone` field for all time-bearing triggers. `run_at` is strictly naive (no tzinfo). IANA validation is performed via `zoneinfo.ZoneInfo`. This is the v2 timezone protocol per spec `2026-04-21-job-timezone-redesign-design.md`.

**`TriggerConfig.immediate()` (2026-06-01):** canonical "fire now" one_off trigger — `run_at` = current UTC wall-clock as a naive datetime + `timezone="UTC"`. Added because `/api/jobs/complex` hand-built `{"trigger_type":"immediate","run_at":utc_now()}`, violating the contract three ways (no such field `trigger_type`; aware `run_at` rejected by `run_at_must_be_naive`; missing `timezone`) so that endpoint failed every time. Always use `immediate()` instead of hand-rolling an immediate-trigger dict.

## Upstream / Downstream

`JobRepository` persists and loads `JobModel`. `JobTrigger` (background service) reads due jobs from the repository and fires them through `AgentRuntime`. `JobModule.hook_after_event_execution()` receives the `PathExecutionResult`, asks the LLM to produce a `JobExecutionResult` (or `OngoingExecutionResult` for ONGOING type), then writes that back to the database via `JobRepository`. The frontend Job panel reads `JobModel` data through `api_schema.JobResponse`.

## Design decisions

**Three job types: `ONE_OFF`, `SCHEDULED`, `ONGOING`**: the first two cover standard task scheduling. `ONGOING` was added in January 2026 for polling/monitoring scenarios (e.g., "keep checking until the customer replies"). ONGOING jobs combine `interval_seconds` with a natural-language `end_condition` that the LLM evaluates after each execution.

**`payload` is natural language, not structured parameters**: the execution instruction is a free-form string assembled into a prompt by `JobTrigger`. This was chosen over structured function calls because different agents have different tool sets and the LLM can interpret intent better from natural language than from rigid parameter schemas.

**`clamp_interval_seconds` validator with a 90-day cap**: LLMs occasionally generate unreasonably large interval values (e.g., scheduling a task "in one year"). The validator silently clamps to 90 days (7,776,000 seconds). Similarly, `clamp_next_run_time` in `JobExecutionResult` caps the next run to 90 days in the future. These guards prevent runaway scheduling.

**`JobExecutionResult` is separate from `JobModel`**: it is a lightweight LLM output struct containing only the fields the LLM needs to fill in after execution. Reusing `JobModel` would expose system management fields (embedding, instance_id, etc.) to the LLM prompt unnecessarily.

**`related_entity_id`** makes the Job execution use a specific user's context. When set, `JobTrigger` loads that user's Narrative and social graph instead of the job creator's context. This enables scenarios like "Agent monitors customer X on behalf of the creator".

## Gotchas

**`JobModel.process` is a list of strings**: it is an append-only execution journal, not a status field. Each run adds 2-5 natural-language step descriptions. Over time this list grows unboundedly. There is no automatic truncation — if a SCHEDULED job runs daily for a year, `process` will have 365+ entries.

**`JobStatus.RUNNING`** is set by `JobTrigger` at execution start and should be cleared to `ACTIVE` or `COMPLETED` when execution finishes. If the process dies mid-execution, the job stays `RUNNING` forever. There is a `started_at` field intended for timeout detection, but as of this writing no automatic stuck-job recovery is implemented.

**`TriggerConfig.cron`** is a standard 5-field cron expression but there is no validation of the expression format. An invalid cron string (e.g., `"0 8 * * * *"` with 6 fields) will be stored successfully and then silently fail to parse at execution time.

**`TriggerConfig.timezone` is now required for all time-bearing triggers**: When you construct a `TriggerConfig` with `run_at`, `cron`, or `interval_seconds` without a `timezone`, Pydantic raises a `ValidationError`. The error message says "timezone is required when run_at / cron / interval_seconds is set." This catches LLM-generated tool calls that omit the timezone field; the MCP agentic loop will retry.

**`TriggerConfig.run_at` must be naive (no tzinfo)**: When you pass a timezone-aware `datetime` (e.g., `datetime(..., tzinfo=timezone.utc)`) as `run_at`, Pydantic raises a `ValidationError` with "naive" in the message. The IANA timezone must be declared separately via the `timezone` field.

**`TriggerConfig.timezone` rejects abbreviations like `"CST"` or `"EST"`**: `zoneinfo.ZoneInfo("CST")` raises `ZoneInfoNotFoundError` because abbreviations are ambiguous. Always use full IANA names like `"America/Chicago"` or `"Asia/Shanghai"`.

## New-joiner traps

- `JobModel.limit` is a field with default `10` that appears to be a pagination hint for the repository. It is stored in the database alongside business data. This field was probably intended for API responses and should not have been on the persistence model.
- `OngoingExecutionResult.should_notify` defaults to `False` for ONGOING jobs. Only the final "completed" execution should notify the user. The LLM is responsible for setting `should_notify=True` only when `should_continue=False`.
- Comparing `job.status == JobStatus.ACTIVE` works because `JobStatus` is `str, Enum`. The string `"active"` and `JobStatus.ACTIVE` are equal.

## 2026-08-19 — TriggerConfig.end_at（recurring 调度地平线）

新增可选字段 `end_at: Optional[datetime]`：recurring（scheduled）job 的
平台级"排到哪天为止"。约定与 `run_at` 完全一致——naive 本地时间 + 由
`timezone` 字段声明时区（同一 validator 拒绝 aware 值；`end_at` 也计入
"time-bearing 字段必须带 timezone"的 model validator）。执行方在
`job_trigger.py` 的 SCHEDULED finalize 分支（谓词
`_job_scheduling.past_schedule_horizon`）：下次 fire 落在地平线之后 →
COMPLETED 而非重排。默认 None = 老 job 逐字不变（铁律 #6）。首个消费方是
onboarding 引导 Agent 的每日 check-in；试用期提醒/倒计时/N 天课程是同一
原语的后续候选。这是调度语义（"日程排到何时"），不是 agent_loop 上限，
不触碰铁律 #14——ONGOING 的 max_iterations 是既有先例。
