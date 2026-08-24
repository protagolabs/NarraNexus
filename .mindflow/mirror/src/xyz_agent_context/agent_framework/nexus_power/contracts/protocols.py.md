---
code_file: src/xyz_agent_context/agent_framework/nexus_power/contracts/protocols.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — ToolChannel.list_tools 的顺序契约写进协议

dispatcher 不再排序(C2 落地)后,「确定性、append-only 顺序」成为**通道的
义务**——新通道若注册序不确定或重排,静默打穿缓存前缀(只表现为命中率掉)。
契约写进 list_tools docstring;真实通道各有顺序断言(BuiltinToolset 构造
确定性、McpToolChannel 批次追加,tests/nexus_power/test_tooling.py)。

# contracts/protocols — 13 个组件 Protocol

「接口一次到位、实现分期长大」的载体;loop 只 import 本文件与 events。判据:加第二个实现时 diff 不碰既有类。LedgerView 只读协议实现读写分离(策略拿不到写账本的刀);CancellationSignal 结构化重声明平台 CancellationView,保住 L0 零平台依赖。

## 2026-08-23(补)— SteeringInlet.take_consumed

`SteeringInlet` 加可选 `take_consumed() -> list[str]`(默认 `[]`):返回自上次调用以来 drain 掉的 steer_inbox 行 id 并清空,
让 loop 能发 `TYPE_STEER_CONSUMED` 报消费、驱动 producer 只对真被读到的行推游标。`QueueSteeringInlet` 实现(累积
被剥的 `_steer_id`),`NullSteeringInlet` 恒空。见消费契约([[message_bus_trigger.py]] 补5)。

## 2026-08-24(补)— take_consumed 无默认体

`SteeringInlet.take_consumed` 方法体改 `...`(不留 `return []`):Protocol 默认体对结构化实现者不生效,而 loop 无条件
调它,所以每个 inlet 必须自定义(下一个——云端 executor 的 inlet——也是)。让「必须实现」在类型层直说。
