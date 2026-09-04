---
code_file: src/narranexus/kernel/plugins/services.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2b.3）— `ServiceRef` 依赖注入（Backstage 式）

插件间调用不走 `import nxplugins.<other>`：提供方 `expose(ref, impl)`，依赖方 `require(ref)`。root 作用域放宿主
自己的服务（`SECRET_STORE`/`EVENT_SINK`/`HOST_VERSION` 三个 ref 常量），插件作用域 `ScopedServices` 只能以
自己的 id expose。`require` 未知 ref 直接 `UnknownEntry` 并列出现有——静默 `None` 正是「装了但什么都不做」
的病根。`release_owner` 在插件停用时收回它暴露的门面。
