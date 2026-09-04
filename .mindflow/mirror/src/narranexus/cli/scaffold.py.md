---
code_file: src/narranexus/cli/scaffold.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2e）— 从 `templates/<kind>/` 拼一个插件

manifest 片段深合并（列表并集、字典合并、标量后者赢）；各 kind 的 `backend/__init__.py` 片段拼接成一个；
其余文件复制并替换 `__PLUGIN_ID__/__PLUGIN_PKG__/__DISPLAY_NAME__/__TABLE_PREFIX__`；补 README/CHANGELOG/
versions.json/tests。测试逐 kind 生成后在子进程里跑生成物自己的测试。

## 2026-09-04 · artifact filter

Template directories accumulate `__pycache__`/`.pyc` and test-run leftovers when their own tests run in place; `_is_artifact` keeps them out of scaffolded plugins (a `.pyc` copied as text raised UnicodeDecodeError).
