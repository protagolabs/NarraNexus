---
code_file: backend/integrations/plugins/spec.py
last_verified: 2026-08-28
stub: false
---

# spec.py — 插件安装的不可变数据契约

## 为什么存在

`InstallComponent` 和 `PluginSpec` 是整个安装子系统唯一的"事实描述"层：
一个插件由哪些安装动作组成、每个动作钉的是哪个版本、怎么判断"已装"、给
用户看哪个版本号。把这层单独摘出来（而不是让 `registry.py` 里直接堆
dict）是为了让 `_installers/` 和 `service.py` 都对着同一套强类型字段编程，
而不是各自约定 key 名字。

## 这个文件不做什么

不含任何安装/探测逻辑——那是 `_installers/` 的职责。也不含具体的两个插件
是谁——那是 `registry.py` 的职责。本文件纯数据形状。

## 上下游关系

- **被谁用**：`registry.py` 用它构造 `PLUGIN_SPECS`；`_installers/*.py` 的
  `install/detect/uninstall` 签名都接收 `InstallComponent`；`service.py`
  遍历 `PluginSpec.components` 派发给对应 installer。
- **依赖谁**：只依赖标准库 `dataclasses`/`typing`，不 import 包内任何其他
  模块——它是这个包的地基,不能反过来依赖上层。

## 设计决策

- 两个 dataclass 都 `frozen=True`：安装契约在一次请求的生命周期里不该被
  任何环节改写（谁改了 requirement 字符串，pip/npm 实际装的版本就和
  registry 声明的对不上,这是要绝对避免的一类 bug）。
- `InstallComponent.requirement` 是"随时可以整串传给 pip/npm 的成品字符串"
  而不是拆开的 name+version 字段——迫使唯一会拼版本号的地方
  （`registry.py`）负责拼对,`_installers/` 只管转发,不重新拼接。

## 相关约束

- 铁律 #9 —— 框架/LLM 不绑定：`PluginSpec` 用 `framework_name` 字段承接
  `agent_framework.plugin_paths._FRAMEWORK_PACKAGE` 的 key,两边靠这个字符
  串对齐,而不是互相 import 对方的常量。
