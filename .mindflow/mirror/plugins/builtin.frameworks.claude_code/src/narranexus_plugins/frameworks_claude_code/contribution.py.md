---
code_file: plugins/builtin.frameworks.claude_code/src/narranexus_plugins/frameworks_claude_code/contribution.py
last_verified: 2026-09-04
stub: false
---

# builtin.frameworks.claude_code — contribution.py

The plugin's `agent.frameworks` contribution (batch 6b.2b): `CONTRIBUTION` names `claude_code`, a lazy factory that activates the plugin pyenv and builds `ClaudeAgentSDK`, and the `FrameworkInstall` spec (npm CLI pin + exact pip pin of `claude-agent-sdk`). The host registers it through the manifest in `narranexus.kernel.plugins.builtins`; the platform never imports this module.
