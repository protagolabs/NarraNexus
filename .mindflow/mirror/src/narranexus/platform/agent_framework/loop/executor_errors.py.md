---
code_file: src/narranexus/platform/agent_framework/loop/executor_errors.py
last_verified: 2026-10-29
stub: false
---

## 2026-10-29 — 新增 `ExecutorTokenRejectedError(ExecutorUnreachableError)`

执行器拒绝本次调用的 API token（401/403）时由 [[remote_driver.py]] 抛出；鉴权失败从来不是用户的错（意味着编排侧与那个容器对 token 不一致：broker 读回失败、跨仓契约漂移）。

**路由靠类名集合，不靠继承**（预审 I-B 订正）：step 3 取 `type(e).__name__` 与 [[failure.py]] 的 `_EXECUTOR_UNREACHABLE_TYPES` 精确比较——是集合成员身份让它 surface 为 `infra_transient`、写审计、不被 helper-LLM 兜底盖住。step 3 **没有** `except ExecutorUnreachableError`，继承只对按类型 except 的调用方有意义。所以以后在这里加新错误类，**必须同时进那个集合**，否则会掉进兜底被编造回复掩盖。

**命名和文案避开 auth 子串**（预审 I-A）：最初叫 `ExecutorAuthError`、文案带 `HTTP 401`——Job 的 `_is_auth_failure` 按 `"401"` 等子串判凭据失效、熔断器的凭据检查看 `"auth" in et`，会把平台故障误判成 owner 凭据过期（Job 被暂停 + 让用户改 provider）。现在类名不含 auth、文案不含状态码，状态码放 `.status`；Job 侧另做了根因修复（infra 类型优先，见 job_trigger）。审计行 `error_type` 保留真实类名，运维可区分。重发通常能恢复：broker 会滚动替换读不回 token 的容器。

## 为什么存在（2026-07-22）

给 per-user executor 传输层一个**类型化**的失败信号 `ExecutorUnreachableError`，
让编排层按异常**类名**分类"executor 基础设施不可达"，而不是对底层
aiohttp/httpx 错误文本做脆弱子串匹配（铁律 #5 治根因）。

抛出点两处：
- [[broker_client.py]] `ensure_executor` / `wait_until_ready`：连不上 broker，或
  容器起来后 `/health` 一直不就绪（冷启动失败）。
- [[loop/remote_driver.py]] `agent_loop`：打到本用户执行容器 `:8020` 的
  连接建立失败（`aiohttp.ClientConnectorError`，只在连接建立时发生，不吞流内错误）。

放在 `agent_framework`（不是 `agent_runtime`）：两个 driver 和上一层的
[[step_3_agent_loop.py]] 都能沿正确依赖方向（orchestration → framework）import，
不产生反向依赖。

## 为什么单开一个类型而不是复用现成异常

- 它是 `RuntimeError` 子类，但类名**不在** [[loop/circuit_breaker.py]] 的
  `_TRANSIENT_ERROR_TYPES` 里 → 不会被当成"重试到底"的瞬时抖动。
- 与"用户 LLM-provider 的连接错误"彻底区分：后者是以 NDJSON `response.error`
  帧出现在流里、由 response_processor 处理的另一类，永远不会变成这个异常。
- 被 [[llm/failure.py]] `classify_executor_infra_failure` 按类名识别 →
  `error_type=infra_transient`（[[runtime_message.py]]），surface 成可读的
  "执行环境异常，请重试/拆小任务"，并 skip helper-LLM 兜底（不被编造回复掩盖）。

携带 `target`（不可达的 URL）供审计 detail 与文案取用，无需再解析 cause。
