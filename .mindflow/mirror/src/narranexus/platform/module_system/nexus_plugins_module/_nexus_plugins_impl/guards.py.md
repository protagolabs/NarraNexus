---
code_file: src/narranexus/platform/module_system/nexus_plugins_module/_nexus_plugins_impl/guards.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2f.1）— 最小护栏集（spec §11.4）为纯函数

id（`<publisher>.<name>`、禁 `builtin.`、禁 protected）、kinds（模板白名单）、路径（必须在
`<workspace>/plugins/<id>` 内；解析已存在前缀跟随 symlink，再拼未存在的尾部，越界/逃逸即拒）、扩展名白名单、
单文件 512 KiB、每 10 分钟窗口 3 次 register/activate、连续两次 rollback 转人工。首版 `check_inside` 在
`plugins/` 目录尚不存在时误判越界（测试实锤），已改为先解析存在前缀再拼尾部。
