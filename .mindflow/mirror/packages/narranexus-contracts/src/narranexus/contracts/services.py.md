---
code_file: packages/narranexus-contracts/src/narranexus/contracts/services.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 三条 ServiceRef 从内核搬来，`ServiceRef` 本身也搬来了（批 6c，A2-3）

`skills.workspaces` / `jobs.instances` / `jobs.run_once` 原来住在
`src/narranexus/kernel/plugins/service_refs.py`——那意味着**内核按名字认识两个业务插件**，
而 `kernel/` 恰恰是宪章 §2.9 里唯一「加实现不许改动」的目录：加第四个跨插件服务
就得改内核。三条 ref 的泛型参数原本还都是 `Any`，等于只有一个字符串键、没有契约。
现在它们带上真类型（`SkillWorkspace` / `JobRunOutcome`），本文件成为它们的家。

`ServiceRef` 这个 dataclass 也一并搬了过来。它是**契约数据**（一个名字加一个类型），
调用两侧（expose 的插件和 require 的插件）本来就都依赖 contracts；
留在内核会逼出一个死结——contracts 是叶子包，import-linter 第一条契约不许它 import
`narranexus.kernel`，连函数体内的延迟 import 也会被 grimp 抓到。
`narranexus.kernel.plugins.services` 现在从这里 re-export 它，
定位器（`ServiceLocator` / `ScopedServices`）仍是内核机械。

同批新增 `WEB_HOST`：插件 router 用的请求作用域宿主 API（见 [[web]]）。

## 2026-09-03（批 1）— `kernel.*` 位的宿主服务契约

`DatabaseBackend` / `SecretStore` / `AuthProvider` / `EventSink` 四个结构化 Protocol，分别是
`kernel.db` / `kernel.secrets` / `kernel.auth` / `kernel.events` 位的契约符号。它们是发行版级选择
（`distribution_only`，插件运行时永不绑定），批 1 只把形状立起来让扩展位树的每个符号真实可 import；
现有实现（`utils/db` 三个方言后端、`secret_box`、本地/NetMind 鉴权、`kernel.events.EventBus`）
在各自包迁入内核时成为默认提供者。方法集刻意最小（按今天的调用面抽象），alpha。
