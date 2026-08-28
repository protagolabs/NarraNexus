---
code_file: src/xyz_agent_context/agent_framework/loop/driver.py
last_verified: 2026-08-28
stub: false
---

## 2026-08-28 — fail-closed：未装插件的框架拒绝构建

新增异常 `FrameworkNotInstalledError(RuntimeError)`（带 `framework` 属性），与
「未知框架」的 `ValueError` 区分：名字合法但其可选 SDK 插件
（claude-agent-sdk/openai-codex）在轻量本地版尚未安装。`get_agent_loop_driver`
在 **remote-executor 短路之后、in-process 建 driver 之前**，**仅对
[[plugin_paths]] `PLUGIN_FRAMEWORKS`（claude_code/codex_cli）成员**查
`framework_installed(name)`，假则抛此异常（nexus_power 与任何自定义注册的 driver
不受门禁——注册即可用）——**绝不静默回落到别的
框架**（否则用户的 agent 会跑在没选的框架上）。只有 in-process 路径会到这；云端走
remote executor 且镜像预装，`framework_installed` 恒真。路由层捕获它、给前端
「去 设置→插件 安装」提示（`framework` 供按框架本地化）。`framework_installed`
用函数内 import 以避与本包 __init__ 的循环依赖。

## 2026-08-24 — remote driver **按 framework 声明 steering**(取代 2026-08-22 节「remote 空集/降级」)

2026-08-22 节的「remote HTTP driver 刻意保持空集、remote run 降级成新 turn」**已反转**。`capabilities()` 的 Protocol docstring 相应改了:remote driver 现对**可 steer 的 framework(nexus_power)返回 `{"steering"}`**、其余返回空集——它经 executor `/steer` + `steer_consumed` 帧承载 steering(见 [[remote_driver.py]] / [[executor_service.py]]),故答案**按 framework 而定**,不是一刀切空集。register 判据 `"steering" in driver.capabilities()` 不变;真正变的是 remote 那条臂现在对 nexus_power 为真。契约测试改名为 `test_steering_capability_is_declared_where_it_can_be_honored`(nexus_power remote 有、claude_code/codex_cli remote 无)。本文件本轮**只改 docstring**。

## 2026-08-22 — 空协商缝有了第一个消费者:steering

PR #351 起,`capabilities()` 不再是"全员空集"。`NexusAgent` 声明
`{"event_log", "steering"}`(见 [[nexus_agent.py]]),remote HTTP driver **刻意**保持空集(活 steer channel
过不了网络,见 [[remote_driver.py]])。三点契约:

- **缝有了消费者**:orchestrator 以 `"steering" in driver.capabilities()` 为 register 判据——本地/桌面(可载活
  channel)起可 steer 的 run,remote 降级成新起 turn。判据是**活的**,不再是"预留空缝"。
- **声明字符串本身就是契约**:拼错(`"Steering"`/`"steer"`)不会报错,只会让 orchestrator 静默判"这个 driver
  不支持 steering",本地路径也一起退化——所以 `driver.py` docstring 把 vocabulary 从"参考"改成了**强制**约束。
- **契约测试形态变了**:`test_driver_contract.py` 那条 `assert caps == set()`(本节旧版说的"钉住整个表面")已
  换成 `test_all_drivers_declare_only_known_vocabulary`(只准声明 planned vocabulary 内的字符串)+
  `test_steering_capability_is_declared_where_it_can_be_honored`(正反两侧钉死;远程臂在 2026-08-24 已从"必须空"
  改成"按 framework:nexus_power 有、claude/codex 无",见上方新节)。`driver.py` 本轮**只改 docstring、零行为改动**,不触发铁律 #10 的阻塞条件——本节是纠正下方 2026-07-27
  节里已被 #351 变假的"全部返回空集/钉住整个表面"两句陈述(Tier-2 是时间序日志,加新节覆盖,不改写历史节)。

## 2026-07-27 — driver 表面一致化：capabilities() 空协商缝 + 签名整形

三个 driver（claude / codex v1+v2 / remote）统一新增 `capabilities() ->
set[str]`（全部返回空集 = 今天的行为；词汇表见 driver.py 注释，只在能力
真正实现的同一变更里声明）。`streaming` 全员改 keyword-only（所有调用点
本就关键字传参，零行为变化）。codex v2 的 `del kwargs` 改为显式 WARNING
（此前 `disallowed_tools` 被静默丢弃——调用方以为约束生效了）。契约测试
`tests/agent_framework/test_driver_contract.py` 钉住整个表面。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-06-17 — Executor seam:per-user `executor_url` 优先,`AGENT_EXECUTOR_URL` 兜底

`get_agent_loop_driver` 新增 keyword-only 参数 `executor_url`:非空时返回
`RemoteAgentLoopDriver` 打到**该用户**的 Executor 容器(由 broker 现取,见
`broker_client.py` + step_3)。未传则回退到静态 env `AGENT_EXECUTOR_URL`;
都没有(本地/桌面,或 executor 容器自身)→ 注册表里的本地 driver,行为不变
(铁律 #7)。优先级:`executor_url` 参数 > `AGENT_EXECUTOR_URL` env > 本地。
这是把 step-3 的 claude/codex spawn 收敛进**每用户隔离容器**的接缝
(铁律 #20 控制面/数据面分离 + per-user 工作区挂载隔离)。

## 2026-06-17 — 默认 framework 名 "claude" → "claude_code"

`DEFAULT_AGENT_LOOP_FRAMEWORK` 从 `"claude"` 改名为 `"claude_code"`，文档串里的 平台默认自 2026-08-20 起为 `nexus_power`（免费/默认用户跑自研 loop）。
fallback 说明同步更新。意图是把默认 driver 的名字对齐到实际注册的
claude-code agent-loop driver（与新引入的 `codex_oauth` 等 provider 形成清晰的
命名空间），避免「默认值写的名字根本没人注册」导致 `get_agent_loop_driver`
当场 ValueError。纯重命名，注册/选择优先级机制不变。

# loop/driver.py — 可插拔 Agent 框架的注册表（铁律 #9 的落地点）

## 为什么存在

step_3 过去直接 `ClaudeAgentSDK(...).agent_loop(...)`，把整个平台焊死在一个
agent 框架上——这正是铁律 #9 警告的"一个开关就崩"。本模块把它变成
「名字 → 工厂」的注册表：以后接入第二个框架（完整的 OpenAI Agents loop、
LangGraph、自研 loop）只需 `register_agent_loop_driver("name", Factory)`，
绝不再改 step_3。

## 两条正交的轴，别混

- **provider 轴**（`provider_driver/`）：用谁的 endpoint / key。
- **framework 轴**（本模块）：用哪套 agent-loop 协议。

两者组合：framework driver 仍通过 provider 层解析 model/endpoint。换模型供应商
动 provider_driver；换 agent SDK 动这里。

## 选择优先级（越具体越优先）

1. `get_agent_loop_driver(framework=...)` 显式传入——per-agent 扩展点。
2. 环境变量 `AGENT_LOOP_FRAMEWORK`。
3. `DEFAULT_AGENT_LOOP_FRAMEWORK` = "claude"。

## 坑

- `ClaudeAgentSDK` 在 `agent_framework` 包导入时（`__init__.py`）自注册为
  "claude"，类本身即工厂（`__init__(working_path=...)` 已符合工厂契约）。
  **导入包这件事才会填充注册表**——定义了但没被导入的 driver 找不到。
- 未知 framework 名 → `get_agent_loop_driver` 抛 ValueError，**不会**静默回退到
  claude。配置写错要当场炸，而不是伪装成默认值。
- `AgentLoopDriver` Protocol 的签名精确镜像 `ClaudeAgentSDK.agent_loop`；那个方法
  就是每个新适配器必须对齐的参考形状（yield 原始事件 dict 给 ResponseProcessor）。
