---
code_file: src/narranexus/kernel/plugins/install/index.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2c）— 官方索引与黑名单（本地缓存一天）

`index.json` 只有元数据（进索引是元数据检查不是代码审查，UI 如实说）；`blocked_versions.json` 启动与安装前
都查。按 ttl 刷新，网络失败用缓存——没网不能让工场页打不开。
