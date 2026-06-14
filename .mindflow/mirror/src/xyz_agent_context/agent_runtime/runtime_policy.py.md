---
code_file: src/xyz_agent_context/agent_runtime/runtime_policy.py
last_verified: 2026-06-14
stub: false
---

# runtime_policy.py — single auditable surface for AgentRuntime variants

## 为什么存在

`AgentRuntime` 主类是"全开"的:加载所有模块、暴露所有 tool、跑所有
hook、可写 awareness、memory 走 agent-scope。这是 owner-facing 路径(chat
WS / Lark / Job / message_bus)需要的行为。

External API protocol(v0.4)需要一个 RESTRICTED 变体 — 跳过
SocialNetworkModule、只读 workspace、不可改 awareness、memory 走
user-scope、prompt 里把 sender 当 visitor 不当 owner。这些限制必须
**集中**在一个文件里,审计时一眼能看完。这就是 `RuntimePolicy`。

未来的 Manyfold mode / replay mode / test mode 也走同样的机制 — 不同
policy 实例,同一套 ExternalAgentRuntime 子类(或它的同族)。

## 上下游关系

**被谁用**:
- [[external_agent_runtime]] — 用 policy 决定加载哪些模块、暴露哪些
  tool、过滤哪些 hook
- [[../module/module_service]] — 接收 policy,把 `skipped_modules` 透传给
  loader,把整个 policy 透传给每个 Module 的构造函数
- [[../module/basic_info_module/basic_info_module]] — 读
  `identity_block_mode` 决定 visitor vs owner 渲染
- [[../module/general_memory_module/general_memory_module]] — 读
  `memory_scope` 决定 retain/recall 用 SCOPE_USER 还是 SCOPE_AGENT
- [[../../../backend/routes/external_api]] — import
  `EXTERNAL_API_POLICY`,通过 `runtime_factory` 传给
  `BackgroundRun`

**依赖谁**: 无 — 纯 dataclass,故意保持零依赖以避免循环 import

## 设计决策

**frozen=True**: policy 实例在请求间共享(尤其
`EXTERNAL_API_POLICY` 是 module-level const),mutation 会引发隐蔽 bug。
frozen 让任何 mutate 早期 fail。

**默认值 = 主 runtime 行为**: 空 `RuntimePolicy()`(即
`DEFAULT_POLICY`)reproduces 今天的 AgentRuntime 行为 — 这样
Module 在拿不到 policy 时(主路径)用 default 就是 backward-compatible
的零行为变化。

**主 AgentRuntime 不读 policy**: 这是核心架构约束(见
[[feedback_agent_runtime_variants]] memory)。policy 只被 subclass 和
policy-aware Module 读取。主 runtime 的 git diff **没有任何**
`if self._policy` 分支。

**为什么不在 module __init__ 里 require policy**: 现有几十个 Module
class 都用 `(agent_id, user_id, db, instance_id, instance_ids)` 5 参数
构造,改成必填会爆。policy 作为 optional kwarg 注入 — 不读它的 module
完全无感。

## Gotchas

**hook_denylist 字符串约定**: 是
`"<ModuleName>.<hook_method_name>"` 形如
`"GeneralMemoryModule.hook_after_event_execution"`,不是单纯的 method
名。这样可以精确到某个 module 的某个 hook,而不会误杀同名 hook
across modules。

**mcp_denylist vs skipped_modules**:
- `skipped_modules` — 模块根本不 instantiate,hook 不跑,MCP 不暴露。
  适合"这模块对外部 session 无意义"的情况(SocialNetworkModule、IM
  channels)。
- `mcp_denylist` — 模块 instantiate,hook 跑(awareness 内容仍注入
  prompt),但 MCP 不暴露(`update_awareness` tool 看不见)。适合
  "保留只读 side effect,禁掉写 side effect"。

**extra_disallowed_tools 走 Claude Code SDK,不是我们的 MCP**:
`Write` / `Edit` / `Bash` 等是 SDK 内置工具(不是 NarraNexus module 的
MCP tool)。这一项通过 `agent_loop(..., disallowed_tools=...)` 直接传给
SDK 的 `ClaudeAgentOptions`,绕过整个 MCP 层。后续 framework
abstraction 层若加新 LLM 提供商,需要在它们各自的 driver 里同样
honor 这一项。

**memory_scope 切换的 owner-facing 影响**: 若有一天决定把
`DEFAULT_POLICY.memory_scope = "user"`(全量切换),owner 跨多个
self-initiated session 的 fact 会变成不再共享。当前主 runtime 路径下
所有 chat/Lark/Job 都用 owner 一个 user_id,所以"per-user scope"="per-
owner scope" 实际无差异 — 这个切换 owner-facing 零回归。这是 v0.5
的 candidate 改动。

