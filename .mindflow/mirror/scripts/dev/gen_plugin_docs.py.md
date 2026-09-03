---
code_file: scripts/dev/gen_plugin_docs.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 从代码生成 `docs/plugins/slots.md`

插件作者的参考文档（契约版本、扩展位树、内置插件清单）由 `narranexus.contracts.API_VERSIONS/
STABILITY`、`build_kernel_slot_tree().to_rows()`、`builtin_manifests()` 渲染，不手写——
spec §13「文档不落后」。`tests/nx_kernel/test_docs_generated.py` 断言提交的文件与 `render()` 逐字
相等，改了扩展位或版本却没跑 `--write` 会红。放在 `scripts/dev/` 与 `narranexus_migrate.py` 同组。
