---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/voice/__init__.py
stub: false
last_verified: 2026-11-19
---

## Why it exists

语音通话子系统的包。2026-11-19 之前这四个文件（`_rtc_voice` / `_voice_delivery` / `_voice_call` / `_voice_control`）
平铺在 `narramessenger_module/` 里，那个目录已有 20 多个文件，而语音这一块还会继续长（打断正在跑的轮次、
`progress.v2`、`dm_report`）。现在趁文件还少一次搬完，后面的新文件直接落在这里。

只是包标记，**不做任何 re-export**：留 re-export 就是给旧路径留兼容层（铁律 #2）。调用方直接 import 具体模块。

## 边界

- [[matrix_trigger.py]] **不在**这个包里，也不该搬进来：`descriptor.py` 的 `trigger_ref` 指向
  `narramessenger_module.matrix_trigger:MatrixTrigger`，那是运行时发现 trigger 的入口，动它等于改部署契约。
- 包内模块互相用同级相对导入（`from ._rtc_voice import ...`），访问渠道其余部分用上一级（`from .._matrix_send import ...`）。
- 四个模块各自的设计理由见各自的 mirror：[[_rtc_voice.py]]、[[_voice_delivery.py]]、[[_voice_call.py]]、[[_voice_control.py]]。
