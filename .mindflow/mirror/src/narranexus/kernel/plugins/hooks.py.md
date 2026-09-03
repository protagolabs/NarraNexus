---
code_file: src/narranexus/kernel/plugins/hooks.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — many 位的机制：HookSpec / HookImpl / HookCaller（pluggy 语义）

与 `registry.py`（one 位、单提供者）互补：一个调用点、任意多插件参与（spec §5.3、D16）。
不引 pluggy 依赖而是自写一份，原因是我们要 async 实现、per-owner 错误隔离、`block(owner)`
与 `Disposable`，pluggy 三者都不给。语义照抄 pluggy 因为它是被数千 pytest 插件验证过的：
LIFO 注册序 + `tryfirst/trylast`；`wrapper` 是恰 yield 一次的生成器（同步或 async 生成器都行），
外层先 before、内层先 after，看到合并后的 `HookOutcome`；`firstresult` 首个非 None 即停；
实现只声明自己用到的参数（`_accepted_params` 按签名裁剪，`**kwargs` 视为全收），所以
spec 加参数不破坏旧插件（参考文档 §B-15）；实现声明了 spec 没有的必填参数在 `add` 时就
`TypeError`，不留到调用期。
错误处理是「隔离不吞」：每个失败记进 `outcome.errors[(owner, exc)]` 并 warning，其余照跑
（宪章 5 复杂度下沉：重试/超时不在这里，在总线/回合预算层）。yield 两次的 wrapper 记错不炸。
`HookRegistry` 是名字→caller 的表：spec 只能 declare 一次（`RegistryConflict`），未声明的名字
`UnknownEntry`（拼错 fail-loud）。批 2 起 `contracts/events.py` 的九个事件在这里 declare。
