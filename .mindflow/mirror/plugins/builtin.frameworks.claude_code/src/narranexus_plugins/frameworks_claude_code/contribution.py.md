---
code_file: plugins/builtin.frameworks.claude_code/src/narranexus_plugins/frameworks_claude_code/contribution.py
last_verified: 2026-09-07
stub: false
---

# builtin.frameworks.claude_code — contribution.py

The plugin's `agent.frameworks` contribution (batch 6b.2b): `CONTRIBUTION` names `claude_code`, a lazy factory that activates the plugin pyenv and builds `ClaudeAgentSDK`, and the `FrameworkInstall` spec (npm CLI pin + exact pip pin of `claude-agent-sdk`). The host registers it through the manifest in `narranexus.kernel.plugins.builtins`; the platform never imports this module.

## 2026-09-07 — META 携带框架事实（B6）

FrameworkMeta 增补 protocol/oauth_source/runtime_name/login_marker（值即原宿主七张名字表里属于本框架的那一行），导出为 META；宿主全部在调用期从注册表派生，本插件是这些事实的唯一持有者。
