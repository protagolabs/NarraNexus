---
code_file: src/narranexus/contracts/ui.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 1）— `ui` 位的 Python 侧契约

前端真正的贡献注册表在 TypeScript（`frontend/src/platform/registries`）；Python 侧只需要给 `ui`
扩展位一个可 import 的契约符号，让发行版能绑定自己的壳。`Shell` 是 backend 提供静态资源所需的
最小数据（构建目录 + 入口文件），纯数据、frozen，默认提供者 `builtin.ui`。
