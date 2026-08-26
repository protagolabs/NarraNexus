---
code_file: tests/agent_runtime/conftest.py
stub: false
last_verified: 2026-08-26
---
# conftest.py — agent_runtime 测试的共享夹具

## `_reset_im_dm_fallback_history`（autouse，整目录）

DM 兜底限流器持有**模块级**状态（`_im_dm_fallback_history`）。重置放在
conftest 而不是单个测试文件里，是因为 `test_im_dm_fallback_delivery_e2e.py`
会跑一次真实投递，因而**在不知情的情况下**往那张 map 里追加条目。

只在测它的那个文件里重置的话，下一个断言这张 map 大小的测试会拿到一个
**依赖执行顺序的初值**——这类 flake 的调试成本远高于一个 autouse 夹具。
