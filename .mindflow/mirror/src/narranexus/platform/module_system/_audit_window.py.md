---
code_file: src/narranexus/platform/module_system/_audit_window.py
last_verified: 2026-10-30
stub: false
---

# module_system/_audit_window.py — 有界计数 + 每窗口一行聚合审计

## 为什么存在

两个身份相关的测量需要同一个形状：[[mcp_auth]] 的 tokenless 调用测量、[[_mcp_identity]] 的
`user_id` 覆盖审计。都是"按 key 计数（key 的部分维度来自调用方）→ 每个窗口往
`instance_executor_audit` 写一行聚合 → 测量失败绝不影响被测调用"。手写两份后已经开始漂移
（截断长度、日志行各不相同），而最容易写错的一条——溢出桶必须塌缩**所有**维度，否则上限只对
一个维度成立（round-5 教训）——存在于两处。现在只有这一份实现，调用方只传参数（auto-review #428）。

## 不变量（属于 helper，不属于调用方）

- 每个维度按各自 cap 截断（只对 str 生效；port 这类服务端 int 不截）。
- 超过 `keys_max` 个不同 key 后，一切并入**一个**常量 key `OVERFLOW_KEY`，不管哪个维度在变；
  dict 上限 `keys_max + 1`。
- `note()` 同步、无 I/O（跑在工具热路径和同步 resolver 里）；合法输入下不会抛，所以**不**包
  try——包住会把将来的真 bug 静音。
- `flush()` 每窗口最多写一次；写库**前**快照并清空（写库期间新到的计数进下一个窗口）；写失败
  只记 debug，丢的是这一窗口的计数，不影响调用。首次 flush 立即执行，所以"有没有发生过"永远
  答得出。返回写出的 entries，供调用方自己打日志（tokenless 的 top-N 日志行留在 mcp_auth）。
- `note` 与 `flush` 分开：tokenless 在 ASGI 中间件里计数后立刻 flush（那里没有"下一次 async
  工具调用"兜底），override 在同步 resolver 里计数、由 async 包装层 flush。两种节奏都要支持。

## 测试

`tests/module/test_audit_window.py` 直接测 helper 的截断、溢出、窗口合并、失败吞掉；两个调用方
各自的有界性测试继续保留。
