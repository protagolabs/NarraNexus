---
code_file: src/narranexus/kernel/plugins/registry.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（预审修订）— `Contribution`、同对象幂等、`owner_of` 统一错误

新增 `Contribution(name, factory, meta)` 与 `register_contribution`；同名且**同一工厂对象**的重复
注册是 no-op（import 期与 manifest 驱动两条路径注册同一对象），不同对象仍 `RegistryConflict`
（除非 `replace=True`）。`owner_of` 对未知名也抛 `UnknownEntry`（之前是裸 KeyError）。

## 2026-09-03 — `Registry[T]`：one 位的唯一注册表形状

范本是 `agent_framework/loop/driver.py` 的框架注册表（名字→惰性工厂、未知名 fail-loud、
确定性顺序），泛化后让 frameworks / provider drivers / memory kinds 与未来所有 `one` 位共用
一份实现和一套测试（spec §5.2）。四条语义是刻意的：
1. `register` 返回 `Disposable`，冻结前可撤销——给测试夹具和插件 deactivate 用；冻结后
   dispose 只记日志，因为注册是启动期活动。
2. 重名默认 `RegistryConflict`（宪章 4「错误定义出存在之外」）；`replace=True` 是给三个遗留
   注册表保留的口子（它们今天允许覆盖：driver 为测试、provider 为夹具、memory 为幂等
   re-import），并在 `Entry.replaced` 留痕供 loader 报告。批 1 收紧。
3. `get` 绝不回退；`UnknownEntry` 同时是 KeyError，遗留 `memory.spec.get_spec` 的调用方不变。
4. `names()` 是注册顺序而非排序：prompt 段落与工具清单从它派生，必须跨重启字节稳定
   （§11 回合路径的缓存前缀）；loader 负责外层「builtin 声明序 + 用户 id 序」。
`normalize` 可选（框架名大小写不敏感就是它）。`__contains__/__len__/__iter__` 让
`"x" in registry` 这类遗留写法直接成立，但**不提供** `__getitem__`/`pop`：那是 dict 的语义，
遗留调用方按 rule 2 改用 `get/try_get/Disposable`。
