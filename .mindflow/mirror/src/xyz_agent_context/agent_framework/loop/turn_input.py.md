---
code_file: src/xyz_agent_context/agent_framework/loop/turn_input.py
last_verified: 2026-07-29
stub: false
---
## 2026-07-29 — resume_session_id 的来源换了(注释同步)

字段本身保留,但**上游不再设置它**:[[step_3_agent_loop]] 的句柄决策已删除(T5)。
现在唯一的填充者是 claude adapter 自己——它每轮写一份 transcript 再 resume
([[transcript]])。字段注释里指向 `_resolve_resume_session_id` 的那句已失效,改为
指向 transcript。

字段之所以还留着:remote 路径仍把它序列化进 `/agent-loop` body(executor 侧
`authorize_resume_session_id` 读它)。那条协议缝隙连同 resume HMAC 一起在 T6 处理。

# loop/turn_input.py — 物化层 turn 输入的显式打包

## 2026-07-28 — 新增 resume_session_id 字段（resume 化 R2）

resume 走 TurnInput 字段而不是调用点裸 kwarg（两案二选一，选前者）：
bundle 本来就是「本轮 driver 输入」的家，且 driver_kwargs() 是唯一出口，
threading 可测。**只在非 None 时发键**：冷启动轮 kwargs 与旧形状字节级
一致；codex 系 driver 永远收不到这个键（step_3 只为 claude_code 解析句
柄），所以 CodexSDKv2 的 ignored-kwargs WARNING 不会被一个恒 None 的字
段每轮刷屏——这正是选 TurnInput + 条件发键而非无条件 kwarg 的原因。
frozen dataclass ⇒ step_3 在 resume 决策之后才构造 TurnInput。

## 为什么存在

step_3 历史上把一个 turn 的输入以四个散落 local（messages / mcp_servers /
skill_env_vars / extra_disallowed_tools）逐个塞给 driver——「物化层」只
存在于调用点的局部变量里。TurnInput 把它变成一个显式对象：所有 driver
可证明地吃同一份东西，且 bundle 有了生长的位置。

## 设计约束

- `driver_kwargs()` **逐字节复刻**历史调用形状：messages/mcp_servers 按
  引用传（step_3 在调用前还会 merge mcp_servers）、空 extra_env /
  disallowed_tools 归一为 None 让 driver 默认值生效。
- `refs` 是预留的引用层字段（TurnContext 双层输入的另一半：自研 loop
  凭可序列化 ID 自己投影上下文）——在有 driver 真正消费之前**恒为
  None**。声明没人实现的字段正是 schema 诚实原则要防的坑。
- `cancellation` 刻意不进 TurnInput：它是 runtime 拥有的每-run 控制流，
  不是 turn 内容。
