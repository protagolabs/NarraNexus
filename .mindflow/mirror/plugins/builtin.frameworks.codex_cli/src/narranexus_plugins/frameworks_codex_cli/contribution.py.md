---
code_file: plugins/builtin.frameworks.codex_cli/src/narranexus_plugins/frameworks_codex_cli/contribution.py
last_verified: 2026-09-07
stub: false
---

# builtin.frameworks.codex_cli — contribution.py

The `agent.frameworks` contribution of the Codex CLI framework: `CONTRIBUTION` = name `codex_cli`, lazy factory building `CodexSDKv2` after `plugin_paths.activate_pyenv()`, and the install spec (npm `@openai/codex` CLI pin + exact pip pin of the Codex SDK). Registered by the kernel from the builtin manifest (batch 6b.2b).

## 2026-09-07 — META 携带框架事实（B6）

FrameworkMeta 增补 protocol/oauth_source/runtime_name/login_marker（值即原宿主七张名字表里属于本框架的那一行），导出为 META；宿主全部在调用期从注册表派生，本插件是这些事实的唯一持有者。
