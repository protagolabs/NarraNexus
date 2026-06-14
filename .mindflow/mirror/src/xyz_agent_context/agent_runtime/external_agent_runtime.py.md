---
code_file: src/xyz_agent_context/agent_runtime/external_agent_runtime.py
last_verified: 2026-06-14
stub: false
---

# external_agent_runtime.py — restricted AgentRuntime for external API

## 为什么存在

External API protocol v0.4 的核心 — 把 `RuntimePolicy`(见
[[runtime_policy]])接到主 `AgentRuntime` 的 7-step pipeline 上,
让外部 session 走 per-user memory scope、visitor identity、AwarenessModule
MCP suppression、Write/Edit/Bash SDK denylist。

**关键约束**:主 `AgentRuntime` 文件零行为改动。Plumbing 是 additive
(`self._policy = None` 默认值 + `ctx.policy = self._policy` 在
`run()` 中一行),全部跟政策有关的分支逻辑都在 subclass / 下游
consumer 里。

未来 Manyfold / replay / test mode 都按同样 shape — 各自 subclass +
各自 policy const,无需再 fork 主 runtime。

## 上下游关系

**被谁用**:
- [[../../../backend/routes/external_api]] 通过
  `make_external_runtime_factory()` 拿到 zero-arg callable,作为
  `runtime_factory` 传给 [[background_run]],由后者在 `drive()` 时
  `async with runtime_factory() as runtime:` 实例化。

**依赖谁**:
- [[agent_runtime]] (subclass 基类)
- [[runtime_policy]] (`RuntimePolicy` + `EXTERNAL_API_POLICY` const)

## 设计决策

**只覆写 `__init__`**: 唯一的差别是 `self._policy = policy`(覆盖
基类设的 None 默认值)。所有 enforcement 由下游 consumer 完成:
- `ModuleService` 读 `policy.skipped_modules` 过滤 MODULE_MAP
- `ModuleLoader` 把 policy 传给每个 module 的构造函数
- `GeneralMemoryModule` / `BasicInfoModule` 读 `self._policy` 分支
  behavior(memory_scope, identity_block_mode)
- `step_3_agent_loop` 读 `ctx.policy.mcp_denylist` 过滤 mcp_urls,
  读 `ctx.policy.extra_disallowed_tools` 拼给 ClaudeAgentSDK

这种 "policy 作为数据,enforcement 散布" 的设计让单点 audit 简单
(读一个 policy const 就知道全部限制),而代码改动局限在各 consumer
里(每个 consumer 加 1~2 行 if 分支)。

**factory pattern**: `BackgroundRun.drive()` 用 `async with` 管理
runtime 生命周期(db client, hook manager 都需要 per-run 实例),
所以 BackgroundRun 接收的是 callable 不是已构造实例。
`make_external_runtime_factory()` 是个 convenience:返回 zero-arg
callable,每次调用产生 fresh ExternalAgentRuntime。

## Gotchas

**子类不要再覆写 `run()` 或 step 函数**: 那样会破坏"主 runtime
零改动"的约束,而且会重复 plumbing(`ctx.policy = self._policy` 那
一行已经把 policy 传下去了,所有 step 通过 ctx 拿)。

**policy frozen**: `EXTERNAL_API_POLICY` 是 module-level const,跨
请求复用,frozen 防止 mutation 引入隐蔽 bug。

**新加 policy 字段时**: 在 [[runtime_policy]] 加字段 → 加 default 值
等于 today's behavior → 找需要响应的 consumer 加 `getattr(self._policy,
"new_field", default)` 分支。**不要**让 consumer 直接 import
`RuntimePolicy` class 做 isinstance 检查 — 那样会引入循环 import 风险。
