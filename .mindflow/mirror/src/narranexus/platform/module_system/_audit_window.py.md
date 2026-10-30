---
code_file: src/narranexus/platform/module_system/_audit_window.py
last_verified: 2026-10-30
stub: false
---

# module_system/_audit_window.py — 有界计数 + 每窗口一行聚合审计

## 为什么存在

两个身份相关的测量需要同一个形状：[[mcp_auth]] 的 tokenless 调用测量、[[_mcp_identity]] 的
`user_id` 覆盖审计。都是"按 key 计数（key 的部分维度来自调用方）→ 每个窗口往
`instance_executor_audit` 写一行聚合 → 测量失败绝不影响被测调用"。手写两份后已经开始漂移，
而最容易写错的一条——溢出桶必须塌缩**所有**维度，否则上限只对一个维度成立（round-5 教训）——
存在于两处。现在只有这一份实现，调用方只描述字段、选参数（auto-review #428）。

## 不变量（属于 helper，不属于调用方）

- **每个维度用一个 `AuditField` 描述一次**：名字、长度上限 `cap`（只对 str 生效；`None` = 不截，
  给 port 这类服务端值用——新增非 str 字段要显式不给 cap）、溢出条目里该字段的 `blank`。三样写在
  一处，而不是几条必须等长、靠人工对齐的平行元组（照 `schema_registry` 的 `Column(...)` 先例）。
- **溢出 key 恒为一个常量** `OVERFLOW_KEY`：超过 `keys_max` 个不同 key 后一切并入它，不管哪个
  维度在变，dict 上限 `keys_max + 1`。这是 helper 的不变量，调用方改不了。调用方能决定的只有
  溢出条目怎么**渲染**：第一个字段固定显示 `"<overflow>"`，其余字段显示各自的 `blank`——用来让
  每个字段在普通行和溢出行里类型一致（tokenless 的 `port` 必须是 int，所以 `blank=0`）。`blank`
  只影响展示，不影响塌缩。
- **`note()` 同步、无 I/O**（跑在工具热路径和同步 resolver 里），只在接错线（key 的段数和字段数
  不符）时抛 `ValueError`——这是必须暴露的编程错误，所以不包 try。
- **`flush()`** 每窗口最多写一次；写库**前**快照并清空（写库期间新到的计数进下一个窗口）；
  把 entries 交给调用方的同步回调 `before_write`，**保证在写库之前**调用，所以人读的日志行既不会被
  慢写库拖延，也不会随写库失败或被取消一起消失；回调抛出、写库失败都只记 debug，丢的是这一窗口
  的日志行或计数，不影响被测调用。首次 flush 立即执行，所以"有没有发生过"永远答得出。返回值
  （entries）只是便利，打日志的通路是 `before_write`。排序时溢出条目固定排最后、不与普通 key
  比较，所以首字段不是 str 也不会在受保护的写库之外抛 TypeError。
- `note` 与 `flush` 分开：tokenless 在 ASGI 中间件里计数后立刻 flush（那里没有"下一次 async
  工具调用"兜底），override 在同步 resolver 里计数、由 async 包装层 flush。两种节奏都要支持。

## 测试

`tests/module/test_audit_window.py` 直接测 helper：截断与 `cap=None`、溢出塌缩成一个常量 key、
溢出条目的默认与自定义 `blank`（`test_overflow_entry_shape_default_and_custom_blanks`）、key 段数
校验（`test_key_must_have_one_part_per_field`）、窗口合并、`reset` 重开窗口、`before_write` 在写库
之前（`test_before_write_runs_before_the_db_write`）、回调或写库失败都被吞掉。两个调用方各自的
有界性测试和 tokenless 溢出行形状测试继续保留。
