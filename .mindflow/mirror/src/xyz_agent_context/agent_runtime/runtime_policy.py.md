---
code_file: src/xyz_agent_context/agent_runtime/runtime_policy.py
last_verified: 2026-06-24
stub: false
---

# runtime_policy.py — 单次 turn 的行为 profile

## 为什么存在

IM channel 把"任何能给 bot 发消息的人"变成 agent 的指令来源；不可信外部访客的
一轮对话不能污染 owner 的持久状态、不能拿到 owner 的密钥/内部标识符。但我们**不能
改主 `AgentRuntime`**（铁律：owner-facing 路径零回归）。`RuntimePolicy` 是这个矛盾的
解法：一个 frozen dataclass，把"这一轮允许碰什么"声明化，挂在 `RunContext` 上，各
step 读 `ctx.policy.<flag>` 自行分支。owner 路径用 `OWNER_POLICY`（全部 flag = off），
等价于加 policy 之前的行为。

## 上下游关系

- **被谁用**：`RunContext`（[[context.py]]）持有一个 `policy` 字段，默认 `OWNER_POLICY`；
  `AgentRuntime.__init__` 设 `self._policy = OWNER_POLICY`，`run()` 把它填进 ctx；
  `StaticVisitorRuntime`（[[static_visitor_runtime.py]]）把 `self._policy` 换成
  `STATIC_VISITOR_POLICY`。各 pipeline step（step_5 跳 hook、context build 注短期记忆、
  env scrub、workspace、tool guard）读 `ctx.policy.*`。
- **依赖谁**：无（纯数据 + 标准库）。故意零依赖，可被任何层 import 而不引入循环。

## 设计决策

**frozen + 模块级单例**：`OWNER_POLICY` / `STATIC_VISITOR_POLICY` 是 frozen 单例，
防止某个 step 误改共享状态污染其它并发 turn。

**字段默认全 = 宽松**：任何一个字段若默认成限制值，都会静默改变 owner 路径。所以
`RuntimePolicy()` 必须 == `OWNER_POLICY`，这条由测试 `test_runtime_policy.py` 锁死。

**`STATIC_VISITOR_POLICY` 只开 v1 真正 enforce 的 flag（2026-06-24 决定）**：v1 开
`skip_after_execution_hooks` / `workspace_mode=scratch` / `im_short_term`；
`scrub_provider_env` / `scrub_internal_ids` / `block_owner_path_writes` **留 False**，
因为它们的 enforce 点要 v2 沙箱（provider key 抹了会断鉴权 → credential proxy；内部 ID
抹除 + owner 路径硬写门都进沙箱）。policy 不能声称自己没兑现的保护。

**只管 DATA/STATE 隔离，不管 code-exec**：这些 flag 控制的是 hook/记忆/凭证/workspace
写——属于状态隔离。**不包含**"访客 Bash 读 workspace 外的文件"这类代码执行隔离，那
需要 OS 沙箱，是 v2 的事（见 `reference/self_notebook/plans/2026-06-22-im-distrust-v1.md`）。

## Gotcha / 边界情况

- 新增 flag 时务必默认成宽松值，并在 `test_owner_policy_is_fully_permissive` 里补一条
  断言，否则 owner 路径会被悄悄改掉。
- `STATIC_VISITOR_POLICY` 的某个 flag 只有在对应 step 真正读它之后才生效；加 flag ≠
  自动生效，必须在那个 step 里接上分支。
