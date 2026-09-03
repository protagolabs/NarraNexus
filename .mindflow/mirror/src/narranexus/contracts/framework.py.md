---
code_file: src/narranexus/contracts/framework.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 1）— `FrameworkInstall` / `InstallComponent`（D7 吸收）

框架的安装配方（pip/npm 组件与钉死版本、探测包名、展示哪个组件的版本、体积提示）成为契约数据
`FrameworkMeta.install`，随 `Contribution.meta["framework"]` 进注册表。dev 分支
`backend/integrations/plugins/registry.py` 那张手写的两条表现在从注册表派生
（`build_plugin_specs`），钉死版本只存在于 `agent_framework/__init__.py` 一处。
`InstallSpec` 改名 `InstallComponent`（与 backend 既有 `InstallComponent` 同名同义）。

## 2026-09-03（补注）— Protocol 里 `capabilities()` 不再带 `return set()` 默认体

Protocol 方法体是 `...`；每个 adapter 都自己实现 `capabilities()`，`runtime_checkable` 只查存在性，
所以没有消费者依赖旧的默认返回。刻意去掉，避免「继承 Protocol 就白得一个空实现」的错觉。

## 2026-09-03 — `AgentLoopDriver` 的正式家（从 loop/driver.py 搬来）

批 0 把 agent-loop 框架的 Protocol 从 `xyz_agent_context/agent_framework/loop/driver.py`
搬到契约层，旧模块 re-export 同一个对象（`tests/nx_kernel/contracts/test_kind_contracts.py`
钉住 `driver.AgentLoopDriver is AgentLoopDriver`）。Protocol 正文与 docstring 逐字保留：
`agent_loop(messages, mcp_servers, *, streaming, extra_env, cancellation, **kwargs)` 异步生成器 +
`capabilities()`。新增两样：`CAPABILITY_VOCABULARY` frozenset（原来只是 docstring 里的词表，
现在是可断言的常量，契约测试基类用它）和 `FrameworkMeta/InstallSpec`（框架的静态描述，
吸收 dev 分支 `backend/integrations/plugins/spec.py` 的 pip/npm 安装描述，D7）。
`AgentEvent` TypedDict（response_processor 的隐式契约）留批 1。
