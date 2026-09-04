---
code_file: src/narranexus/platform/turn/stages/__init__.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— 默认策略的注册

七个阶段的 `Contribution` 常量（`INGRESS/RECALL/COMPOSE/ASSEMBLE/ACT/COMMIT/REFLECT`）在 import 时注册进
`KERNEL_REGISTRIES`（缺位则以 owner `builtin.turn` 声明），与 `builtin.turn` manifest 的 provides 是同一批对象，
loader 再注册是幂等 no-op（与 frameworks/providers 同一模式）。`ensure_registered(registries)` 让测试用干净
`Registries` 也能起流水线。
