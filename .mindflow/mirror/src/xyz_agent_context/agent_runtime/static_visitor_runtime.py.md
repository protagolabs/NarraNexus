---
code_file: src/xyz_agent_context/agent_runtime/static_visitor_runtime.py
last_verified: 2026-06-24
stub: false
---

# static_visitor_runtime.py — 不可信外部 IM 访客的 runtime 变体（distrust v1）

## 为什么存在

不可信外部 IM 访客的一轮对话需要一套受限行为（跳 after-execution hooks、scratch
workspace、抹凭证/内部 ID、挡 owner 路径写、走 IM 短期记忆）。铁律要求"新 runtime
模式 = `AgentRuntime` 子类 + `RuntimePolicy`，绝不改主 runtime"。`StaticVisitorRuntime`
就是这个子类：**极薄**，完整继承 `run()` 流水线，只把 `self._policy` 换成
`STATIC_VISITOR_POLICY`。所有行为差异都由 policy 声明、由各 step 读取，子类本身不含
任何分支逻辑。

## 上下游关系

- **被谁用**：channel trigger 在"binding 不可信且 sender ≠ owner"时实例化它（替代
  `AgentRuntime`）。路由判定见 v1 plan 的 T8。
- **依赖谁**：父类 `AgentRuntime`（[[agent_runtime.py]]）；`RuntimePolicy` /
  `STATIC_VISITOR_POLICY`（[[runtime_policy.py]]）。

## 设计决策

**构造接受 `policy` 参数**：默认 `STATIC_VISITOR_POLICY`，但允许传更严的 profile，
未来出现 v1.x 更严档位时不必再开子类。

**自带 `run_and_collect`（2026-06-24）**：trigger 对 distrust turn **直接** `await
StaticVisitorRuntime(...).run_and_collect(...)`，而不是走
`get_agent_runtime_client()`（它恒构造普通 `AgentRuntime`，丢掉 policy）。该方法镜像
`InProcessAgentRuntimeClient.run_and_collect`，但驱动的是 `self` → distrust policy 跟着
整条 run 走。仍走 `admission.slot(user_id=owner)` 闸（owner 付费、owner 计资源）。
import 局部化避开 channel/__init__ ↔ AgentRuntime 循环导入。

**不 override `run()`**：刻意只覆盖 policy。这样主流水线的任何演进自动惠及 distrust
路径，且 owner 路径与 distrust 路径走的是同一份代码，只是 policy 不同——降低两条路
行为漂移的风险。

## Gotcha / 边界情况

- 计费仍落 owner：父类 `run()` 里 `user_id → agents.created_by` 的 override 和基于
  agent owner 的 LLM 配置解析都没动，distrust turn 用的是 owner 的额度。
- 这个子类**不提供 code-exec 隔离**：访客 Bash 仍可读 workspace 外的文件。硬隔离要
  v2 的 OS 沙箱。
