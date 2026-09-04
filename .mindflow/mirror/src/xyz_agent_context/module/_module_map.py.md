---
code_file: src/xyz_agent_context/module/_module_map.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3c.1）— `ModuleMapView`：注册表支撑的只读 MODULE_MAP

所有 `MODULE_MAP[name]` / `in` / `.items()` 消费方不改一行；真相搬到内核 `agent.capabilities.modules`
注册表。按注册表名字集缓存构建结果；导入失败的模块缺席（warning）而不炸；`meta()`/`owner_of()` 给派生表用。
被禁用的内置在启动时被 `remove_owner` 移出注册表，于是从视图消失。
