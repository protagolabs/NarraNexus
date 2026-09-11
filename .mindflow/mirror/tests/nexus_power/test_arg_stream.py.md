---
code_file: tests/nexus_power/test_arg_stream.py
last_verified: 2026-09-11
stub: false
---
# tests/arg_stream — 任意切分下的流式抽取

逐字符切分模糊、跨界转义/\u、嵌套遮蔽、数组不腐蚀键、finalize 校齐不变量。

## 2026-09-11 — emoji / surrogate 用例

ensure_ascii 形态的 emoji 在任意切分下合成正确且严格可 UTF-8 编码；孤立高/低位与「高高低」序列输出 U+FFFD；逐切点 feed+finalize
对含 emoji 的值保持「流出 == 最终值」；`scrub_surrogates` 单测。回退合并逻辑时这些用例变红。
