---
code_file: src/narranexus/contracts/services.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 1）— `kernel.*` 位的宿主服务契约

`DatabaseBackend` / `SecretStore` / `AuthProvider` / `EventSink` 四个结构化 Protocol，分别是
`kernel.db` / `kernel.secrets` / `kernel.auth` / `kernel.events` 位的契约符号。它们是发行版级选择
（`distribution_only`，插件运行时永不绑定），批 1 只把形状立起来让扩展位树的每个符号真实可 import；
现有实现（`utils/db` 三个方言后端、`secret_box`、本地/NetMind 鉴权、`kernel.events.EventBus`）
在各自包迁入内核时成为默认提供者。方法集刻意最小（按今天的调用面抽象），alpha。
