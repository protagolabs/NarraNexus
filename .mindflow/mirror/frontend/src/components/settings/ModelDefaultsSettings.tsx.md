---
code_file: frontend/src/components/settings/ModelDefaultsSettings.tsx
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 — global default model editor (Settings › Model Defaults)

The provider + model + coding-agent framework every agent INHERITS by default —
extracted out of [[ProviderSettings]]' old "Section ③" so LLM Providers is purely
the credential wallet. Rendered under the new "Model Defaults" nav item
([[SettingsPage]]).

Edits two user-level slots and writes via the unchanged endpoints:
`PUT /api/providers/slots/{agent|helper_llm}` (`api.setProviderSlot`) +
`POST /api/providers/agent-framework` (`api.setAgentFramework`). The framework
switch persists immediately (it may auto-install codex + re-probe auth) and
clears the agent provider/model on a protocol change; the two slots save
together on "Save defaults" (writes only the changed slots). Option-building is
shared via [[agentFramework]] (`getModelsForSlot` / `AGENT_FRAMEWORKS` /
`CODEX_ALLOWED_PROVIDER_SOURCES`) so the choices match the per-agent panel
([[AgentLlmConfigPanel]]) and the provider dropdowns.

Structurally close to the per-agent [[AgentLlmConfigPanel]] (agent framework +
model + reasoning, helper model), but inline (not a modal) and writing the
user-level default instead of a per-agent override. Panel copy points users to
the chat page for per-agent overrides. Empty state when no providers exist:
prompts to add one under LLM Providers first.

Note: the cloud staff-gate on framework switching is enforced by the backend
(403 on `setAgentFramework`); this panel surfaces the error rather than
pre-fetching claude/codex status to render a read-only display (a lighter
version of the old renderSlotRow behavior).
