---
code_file: src/narranexus/platform/agent_framework/loop/executor_errors.py
last_verified: 2026-10-29
stub: false
---

## 2026-10-29 — 新增 `ExecutorAuthError(ExecutorUnreachableError)`

执行器拒绝本次调用的 API token（401/403）时由 [[remote_driver.py]] 抛出。**为什么是子类**：鉴权失败从来不是用户的错（意味着编排侧与那个容器对 token 不一致：broker 读回失败、跨仓契约漂移），必须走与「不可达」同一条平台故障路径——surface infra_transient、写审计、**不**被 helper-LLM 兜底编造回复盖住；step 3 现有的 `except ExecutorUnreachableError` 直接覆盖它。审计行的 `error_type` 保留真实类名，运维可区分。类名同样不进熔断器的 transient 集合。重发通常能恢复：broker 会滚动替换读不回 token 的容器。

# executor_errors.py — executor 传输边界的类型化异常

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
