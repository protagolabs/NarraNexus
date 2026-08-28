---
code_file: tests/channel/test_ingress_guard_all_paths.py
stub: false
last_verified: 2026-08-24
---
# test_ingress_guard_all_paths.py — 每条入站路径都要经过守卫

没有单一 chokepoint 可放守卫，所以 seam 是**方法**（`_ingress_admitted`），
而「每条接收路径都调它」这条不变量，类型系统和任何单元测试都拿不住。
与 [[test_trigger_envelope_every_channel.py]] 同一个缺陷类、同一个答案。

**它第一次跑就见了血**：抓出 Telegram / WeChat / Matrix 三个此前没被数进来的
`_process_message` override（都调了 `super()`，所以实际是安全的，但调研阶段
根本没数到它们）。同时暴露出 Matrix 的 `group_silent` 分支在 `super()` 之前
就 return，而那条路仍然跑记忆管线——真漏了一处，因此被补上。

**断言必须防散文**：第一版用 `inspect.getsource` 直接 grep 字符串，结果
两处**注释文字**把断言骗过去了（Lark 里一句「never calls
`super()._process_message`」让「它有没有委托？」答成了是）。所以现在先剥掉
`#` 注释行，再匹配**带左括号**的调用形式。
