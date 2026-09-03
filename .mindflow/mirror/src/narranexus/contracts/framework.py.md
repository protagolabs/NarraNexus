---
code_file: src/narranexus/contracts/framework.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — `AgentLoopDriver` 的正式家（从 loop/driver.py 搬来）

批 0 把 agent-loop 框架的 Protocol 从 `xyz_agent_context/agent_framework/loop/driver.py`
搬到契约层，旧模块 re-export 同一个对象（`tests/nx_kernel/contracts/test_kind_contracts.py`
钉住 `driver.AgentLoopDriver is AgentLoopDriver`）。Protocol 正文与 docstring 逐字保留：
`agent_loop(messages, mcp_servers, *, streaming, extra_env, cancellation, **kwargs)` 异步生成器 +
`capabilities()`。新增两样：`CAPABILITY_VOCABULARY` frozenset（原来只是 docstring 里的词表，
现在是可断言的常量，契约测试基类用它）和 `FrameworkMeta/InstallSpec`（框架的静态描述，
吸收 dev 分支 `backend/integrations/plugins/spec.py` 的 pip/npm 安装描述，D7）。
`AgentEvent` TypedDict（response_processor 的隐式契约）留批 1。
