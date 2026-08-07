---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/tooling/policy.py
last_verified: 2026-08-07
stub: false
---

## 2026-08-07 — extra_readable_roots：协作区放行（团队共享工作台前置）

confinement 从「只认 workspace」改为「workspace **+ 平台声明的额外根**」（`_permitted_roots`
/ `_within_permitted`，两个 layer 共用）。动机是一个当时就存在的自相矛盾：team prompt
（[[message_bus_trigger.py]]）明写让 agent 用 Read 打开 `_shared/teams/{id}`，而该目录
按设计是 agent workspace 的 **sibling**（谁都不拥有它）→ 必然越界 → DENY；claude/codex
没有这层，三框架两种行为。

fail-closed 未被削弱：框架**从不自行推导**根目录——推导 `workspace.parent/_shared` 在
旧 flat 布局（`{agent_id}_{user_id}`）下会解析成整个 base，放行面直接失控。根由知道
user 的调用方显式传入，解析失败的根被丢弃而非放宽。相对路径仍只相对 workspace 解析，
额外根只能用绝对路径抵达。空元组（缺省）= 逐字复现旧行为。

# tooling/policy — fail-closed 策略引擎

有序 layer、deny 永远赢、layer 崩溃即拒(多租户自研决策,业界无背书,Codex fail-open 是反面)。WorkspaceConfinement 只查内建工具路径参数(mcp__ 的副作用在服务端),拒绝并点名路径,不静默改写。
